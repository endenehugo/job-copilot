from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, asc, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.repositories.mysql_base import Base


class ConversationDocument(Base):
    __tablename__ = "conversation_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(500), nullable=False)
    parsed_text_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class DocumentRepository:
    def create(self, session: Session, **kwargs) -> ConversationDocument:
        entity = ConversationDocument(**kwargs)
        session.add(entity)
        session.flush()
        return entity

    def get_by_document_id(self, session: Session, document_id: str) -> ConversationDocument | None:
        stmt = select(ConversationDocument).where(ConversationDocument.document_id == document_id)
        return session.execute(stmt).scalar_one_or_none()

    def list_by_conversation_id(self, session: Session, conversation_id: str) -> list[ConversationDocument]:
        stmt = (
            select(ConversationDocument)
            .where(ConversationDocument.conversation_id == conversation_id)
            .order_by(asc(ConversationDocument.created_at), asc(ConversationDocument.id))
        )
        return list(session.execute(stmt).scalars())

    def delete(self, session: Session, document: ConversationDocument) -> None:
        session.delete(document)