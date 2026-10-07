from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text, desc, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.repositories.mysql_base import Base


class JobAnalysis(Base):
    __tablename__ = "job_analysis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # JD 原文
    jd_text: Mapped[str] = mapped_column(Text, nullable=False)

    # JD 解析结果
    job_role: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    keywords: Mapped[str] = mapped_column(Text, nullable=False, default="[]")       # JSON array
    requirements: Mapped[str] = mapped_column(Text, nullable=False, default="[]")    # JSON array
    bonus_points: Mapped[str] = mapped_column(Text, nullable=False, default="[]")    # JSON array

    # 匹配评分
    total_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    skill_match_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    project_relevance_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    expression_quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    job_fitness_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # 优势与缺口
    strengths: Mapped[str] = mapped_column(Text, nullable=False, default="[]")       # JSON array
    gaps: Mapped[str] = mapped_column(Text, nullable=False, default="[]")            # JSON array
    suggestions: Mapped[str] = mapped_column(Text, nullable=False, default="[]")     # JSON array

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class JobAnalysisRepository:
    @staticmethod
    def create(session: Session, **kwargs) -> JobAnalysis:
        entity = JobAnalysis(**kwargs)
        session.add(entity)
        session.flush()
        return entity

    @staticmethod
    def get_by_id(session: Session, analysis_id: int) -> JobAnalysis | None:
        stmt = select(JobAnalysis).where(JobAnalysis.id == analysis_id)
        return session.execute(stmt).scalar_one_or_none()

    @staticmethod
    def list_by_conversation_id(session: Session, conversation_id: str, limit: int = 20) -> list[JobAnalysis]:
        stmt = (
            select(JobAnalysis)
            .where(JobAnalysis.conversation_id == conversation_id)
            .order_by(desc(JobAnalysis.created_at), desc(JobAnalysis.id))
            .limit(limit)
        )
        return list(session.execute(stmt).scalars())

    @staticmethod
    def get_latest_by_conversation_id(session: Session, conversation_id: str) -> JobAnalysis | None:
        stmt = (
            select(JobAnalysis)
            .where(JobAnalysis.conversation_id == conversation_id)
            .order_by(desc(JobAnalysis.created_at), desc(JobAnalysis.id))
            .limit(1)
        )
        return session.execute(stmt).scalar_one_or_none()
