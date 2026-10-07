from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.repositories.mysql_base import Base


class KnowledgeEntry(Base):
    """知识库动态条目：经 AI 审核后入库的内容（内置 23 条之外的增量）。"""

    __tablename__ = "knowledge_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="general")
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # source: user（用户提交）/ ai（AI 自主扩充）
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    # status: approved（审核通过，参与检索）/ rejected（未通过，仅留审计）
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="approved")
    review_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class KnowledgeEntryRepository:
    @staticmethod
    def create(session: Session, **kwargs) -> KnowledgeEntry:
        entity = KnowledgeEntry(**kwargs)
        session.add(entity)
        session.flush()
        return entity

    @staticmethod
    def get_by_entry_id(session: Session, entry_id: str) -> KnowledgeEntry | None:
        from sqlalchemy import select
        stmt = select(KnowledgeEntry).where(KnowledgeEntry.entry_id == entry_id)
        return session.execute(stmt).scalar_one_or_none()

    @staticmethod
    def list_by_status(session: Session, status: str, limit: int = 100) -> list[KnowledgeEntry]:
        from sqlalchemy import desc, select
        stmt = (
            select(KnowledgeEntry)
            .where(KnowledgeEntry.status == status)
            .order_by(desc(KnowledgeEntry.id))
            .limit(limit)
        )
        return list(session.execute(stmt).scalars())

    @staticmethod
    def delete(session: Session, entity: KnowledgeEntry) -> None:
        session.delete(entity)
