from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, asc, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.repositories.mysql_base import Base


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # index=True 只对 create_all 新建的表生效；存量表的补齐见 mysql_base._INDEX_PATCHES
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class MessageRepository:
    def create(self, session: Session, **kwargs) -> ConversationMessage:
        entity = ConversationMessage(**kwargs)
        session.add(entity)
        session.flush()
        return entity

    def list_by_conversation_id(self, session: Session, conversation_id: str) -> list[ConversationMessage]:
        stmt = (
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(asc(ConversationMessage.created_at), asc(ConversationMessage.id))
        )
        return list(session.execute(stmt).scalars())

    def list_after_id(self, session: Session, conversation_id: str, after_id: int) -> list[ConversationMessage]:
        """只查 id > after_id 的消息，用于增量加载。"""
        stmt = (
            select(ConversationMessage)
            .where(
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.id > after_id,
            )
            .order_by(asc(ConversationMessage.id))
        )
        return list(session.execute(stmt).scalars())