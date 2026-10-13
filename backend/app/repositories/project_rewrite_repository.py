from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, desc, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.repositories.mysql_base import Base


class ProjectRewrite(Base):
    """项目经历优化结果（2026-10-13 迁移到会话记录时新增的持久化）。

    旧版 /resume/project/rewrite 纯函数式返回、不落库，结果刷新即丢；
    迁移到会话树后每次优化作为会话的子节点留存，因此补建本表。
    """

    __tablename__ = "project_rewrites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    original_description: Mapped[str] = mapped_column(Text, nullable=False)
    # JSON array: 原描述存在的问题列表
    original_issues: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    improved_version: Mapped[str] = mapped_column(Text, nullable=False, default="")
    python_backend_version: Mapped[str] = mapped_column(Text, nullable=False, default="")
    agent_version: Mapped[str] = mapped_column(Text, nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ProjectRewriteRepository:
    @staticmethod
    def create(session: Session, **kwargs) -> ProjectRewrite:
        entity = ProjectRewrite(**kwargs)
        session.add(entity)
        session.flush()
        return entity

    @staticmethod
    def get_by_id(session: Session, rewrite_id: int) -> ProjectRewrite | None:
        stmt = select(ProjectRewrite).where(ProjectRewrite.id == rewrite_id)
        return session.execute(stmt).scalar_one_or_none()

    @staticmethod
    def list_by_conversation_id(session: Session, conversation_id: str, limit: int = 20) -> list[ProjectRewrite]:
        stmt = (
            select(ProjectRewrite)
            .where(ProjectRewrite.conversation_id == conversation_id)
            .order_by(desc(ProjectRewrite.created_at), desc(ProjectRewrite.id))
            .limit(limit)
        )
        return list(session.execute(stmt).scalars())

    @staticmethod
    def delete(session: Session, entity: ProjectRewrite) -> None:
        session.delete(entity)
