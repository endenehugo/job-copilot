"""消息的结构化载荷（嵌入卡片）。

为什么单独一张表而不是给 `conversation_messages` 加列：
项目没有 Alembic，`Base.metadata.create_all` 只建缺失的表、不会 ALTER 存量表。
一旦把 `meta` 列写进 ORM 模型，**消息列表的 SELECT 就会带上这一列**，
补列失败（缺 DDL 权限）时整个会话的历史消息都查询失败——锦上添花的功能把核心链路带崩。
独立新表由 `create_all` 自动创建，零 DDL 风险，列不存在时最多是卡片不显示。

一条消息最多一条载荷记录；`kind` 区分卡片类型（resume / 未来的其它），
新增卡片类型不需要再改表结构。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.repositories.mysql_base import Base


class ConversationMessageMeta(Base):
    __tablename__ = "conversation_message_meta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class MessageMetaRepository:
    @staticmethod
    def create(session: Session, **kwargs) -> ConversationMessageMeta:
        entity = ConversationMessageMeta(**kwargs)
        session.add(entity)
        session.flush()
        return entity

    @staticmethod
    def get_by_message_id(session: Session, message_id: str) -> ConversationMessageMeta | None:
        stmt = select(ConversationMessageMeta).where(ConversationMessageMeta.message_id == message_id)
        return session.execute(stmt).scalar_one_or_none()

    @staticmethod
    def map_payloads_by_conversation_id(session: Session, conversation_id: str) -> dict[str, str]:
        """一次查出整段会话的载荷：{message_id: payload_json}。

        消息列表是高频接口，逐条查询会变成 N+1。
        """
        stmt = select(
            ConversationMessageMeta.message_id, ConversationMessageMeta.payload_json
        ).where(ConversationMessageMeta.conversation_id == conversation_id)
        return {message_id: payload or "{}" for message_id, payload in session.execute(stmt).all()}

    @staticmethod
    def delete_by_conversation_id(session: Session, conversation_id: str) -> None:
        from sqlalchemy import delete as sql_delete

        session.execute(
            sql_delete(ConversationMessageMeta).where(
                ConversationMessageMeta.conversation_id == conversation_id
            )
        )
