from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, desc, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.repositories.mysql_base import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="新对话")
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="agent")
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_message_preview: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    context_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    summarized_until_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # 置顶时间；NULL = 未置顶。用时间而不是布尔，留出"按置顶先后排"的余地。
    # 注意：这是后加的列，create_all 不会给已有表补，靠 mysql_base._ensure_columns 兜底。
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    # 归属标识，取值见 core/ownership.py。空串 = 归属功能上线前的历史会话，
    # 只有管理员可见——用"空值语义"代替一次数据迁移。
    owner_key: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)


class ConversationRepository:
    def create(self, session: Session, **kwargs) -> Conversation:
        entity = Conversation(**kwargs)
        session.add(entity)
        session.flush()
        return entity

    def get_by_conversation_id(self, session: Session, conversation_id: str) -> Conversation | None:
        stmt = select(Conversation).where(Conversation.conversation_id == conversation_id)
        return session.execute(stmt).scalar_one_or_none()

    def list_recent(
        self, session: Session, limit: int = 50, owner_keys: list[str] | None = None
    ) -> list[Conversation]:
        """按活跃度取最近会话。

        `owner_keys` 为 None 表示不过滤（管理员视角）；否则只取归属在这组里的行。
        传空列表会得到空结果而不是全表——这是有意的：调用方拿不到身份时
        应该"看不到"，不该"看到全部"。

        排序必须在 SQL 里做：置顶的会话可能很旧，取回 50 条再在内存里排会把它截掉。
        pinned_at IS NULL → 0（置顶）在前、1（未置顶）在后；置顶之间按置顶时间倒序。
        """
        stmt = select(Conversation)
        if owner_keys is not None:
            stmt = stmt.where(Conversation.owner_key.in_(owner_keys))
        stmt = (
            stmt.order_by(
                Conversation.pinned_at.is_(None),
                desc(Conversation.pinned_at),
                desc(Conversation.updated_at),
            )
            .limit(limit)
        )
        return list(session.execute(stmt).scalars())

    def set_pinned(
        self, session: Session, conversation: Conversation, pinned: bool, now: datetime | None = None
    ) -> Conversation:
        """置顶/取消置顶。

        故意不动 updated_at：置顶不是"活跃"，改写它会让取消置顶后位置回不去。
        重复置顶保留首次时间，保证已置顶项之间的次序稳定。
        """
        if not pinned:
            conversation.pinned_at = None
        elif conversation.pinned_at is None:
            conversation.pinned_at = now or datetime.now()
        return conversation

    def update_summary(
        self,
        conversation: Conversation,
        *,
        title: str | None = None,
        message_count: int | None = None,
        last_message_preview: str | None = None,
        updated_at: datetime | None = None,
        mode: str | None = None,
    ) -> Conversation:
        if title is not None:
            conversation.title = title
        if message_count is not None:
            conversation.message_count = message_count
        if last_message_preview is not None:
            conversation.last_message_preview = last_message_preview
        if updated_at is not None:
            conversation.updated_at = updated_at
        if mode is not None:
            conversation.mode = mode
        return conversation

    def update_context_summary(
        self,
        session: Session,
        conversation_id: str,
        summary: str,
        until_id: int,
    ) -> bool:
        """写入增量摘要，乐观锁：仅当 until_id 前进时才更新。"""
        conv = self.get_by_conversation_id(session, conversation_id)
        if conv is None:
            return False
        if until_id <= conv.summarized_until_id:
            return False
        conv.context_summary = summary
        conv.summarized_until_id = until_id
        return True