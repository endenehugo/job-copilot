from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, desc, select
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
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ConversationRepository:
    def create(self, session: Session, **kwargs) -> Conversation:
        entity = Conversation(**kwargs)
        session.add(entity)
        session.flush()
        return entity

    def get_by_conversation_id(self, session: Session, conversation_id: str) -> Conversation | None:
        stmt = select(Conversation).where(Conversation.conversation_id == conversation_id)
        return session.execute(stmt).scalar_one_or_none()

    def list_recent(self, session: Session, limit: int = 50) -> list[Conversation]:
        stmt = select(Conversation).order_by(desc(Conversation.updated_at)).limit(limit)
        return list(session.execute(stmt).scalars())

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