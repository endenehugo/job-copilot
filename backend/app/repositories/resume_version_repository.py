from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text, desc, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.repositories.mysql_base import Base


class ResumeVersion(Base):
    __tablename__ = "resume_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    document_id: Mapped[str] = mapped_column(String(64), nullable=False)

    # 版本信息
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 对应的分析结果（冗余，方便快速展示）
    total_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    skill_match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    project_relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    expression_quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    job_fitness_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ResumeVersionRepository:
    @staticmethod
    def create(session: Session, **kwargs) -> ResumeVersion:
        entity = ResumeVersion(**kwargs)
        session.add(entity)
        session.flush()
        return entity

    @staticmethod
    def get_by_version_id(session: Session, version_id: str) -> ResumeVersion | None:
        stmt = select(ResumeVersion).where(ResumeVersion.version_id == version_id)
        return session.execute(stmt).scalar_one_or_none()

    @staticmethod
    def list_by_conversation_id(session: Session, conversation_id: str, limit: int = 20) -> list[ResumeVersion]:
        stmt = (
            select(ResumeVersion)
            .where(ResumeVersion.conversation_id == conversation_id)
            .order_by(desc(ResumeVersion.version_number), desc(ResumeVersion.created_at))
            .limit(limit)
        )
        return list(session.execute(stmt).scalars())

    @staticmethod
    def get_latest_by_conversation_id(session: Session, conversation_id: str) -> ResumeVersion | None:
        stmt = (
            select(ResumeVersion)
            .where(ResumeVersion.conversation_id == conversation_id)
            .order_by(desc(ResumeVersion.version_number), desc(ResumeVersion.created_at))
            .limit(1)
        )
        return session.execute(stmt).scalar_one_or_none()

    @staticmethod
    def get_max_version_number(session: Session, conversation_id: str) -> int:
        stmt = (
            select(ResumeVersion.version_number)
            .where(ResumeVersion.conversation_id == conversation_id)
            .order_by(desc(ResumeVersion.version_number))
            .limit(1)
        )
        result = session.execute(stmt).scalar_one_or_none()
        return result or 0

    @staticmethod
    def update_scores(
        session: Session,
        entity: ResumeVersion,
        total_score: float,
        dimensions: dict,
    ) -> ResumeVersion:
        entity.total_score = total_score
        entity.skill_match_score = dimensions.get("skill_match", 0)
        entity.project_relevance_score = dimensions.get("project_relevance", 0)
        entity.expression_quality_score = dimensions.get("expression_quality", 0)
        entity.job_fitness_score = dimensions.get("job_fitness", 0)
        session.flush()
        return entity

    @staticmethod
    def delete(session: Session, entity: ResumeVersion) -> None:
        session.delete(entity)
