from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, desc, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.repositories.mysql_base import Base


class InterviewSession(Base):
    __tablename__ = "interview_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # 关联的 JD 分析和简历
    job_analysis_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    job_role: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    direction: Mapped[str] = mapped_column(String(32), nullable=False, default="general")

    # 面试状态: in_progress / completed
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="in_progress")
    round_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_score: Mapped[float | None] = mapped_column(Integer, nullable=True)

    # JSON: 初始面试题列表
    initial_questions: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    overall_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class InterviewMessage(Base):
    __tablename__ = "interview_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # role: system / user / assistant
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # 如果是 assistant，附加评分和评价
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evaluation: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # 消息类型: question / answer / evaluation / summary
    msg_type: Mapped[str] = mapped_column(String(32), nullable=False, default="question")

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class InterviewSessionRepository:
    @staticmethod
    def create(session: Session, **kwargs) -> InterviewSession:
        entity = InterviewSession(**kwargs)
        session.add(entity)
        session.flush()
        return entity

    @staticmethod
    def get_by_session_id(session: Session, session_id: str) -> InterviewSession | None:
        stmt = select(InterviewSession).where(InterviewSession.session_id == session_id)
        return session.execute(stmt).scalar_one_or_none()

    @staticmethod
    def list_by_conversation_id(session: Session, conversation_id: str, limit: int = 20) -> list[InterviewSession]:
        stmt = (
            select(InterviewSession)
            .where(InterviewSession.conversation_id == conversation_id)
            .order_by(desc(InterviewSession.created_at), desc(InterviewSession.id))
            .limit(limit)
        )
        return list(session.execute(stmt).scalars())

    @staticmethod
    def update(session: Session, entity: InterviewSession, **kwargs) -> InterviewSession:
        for key, value in kwargs.items():
            if hasattr(entity, key):
                setattr(entity, key, value)
        session.flush()
        return entity

    @staticmethod
    def delete(session: Session, entity: InterviewSession) -> None:
        session.delete(entity)


class InterviewMessageRepository:
    @staticmethod
    def create(session: Session, **kwargs) -> InterviewMessage:
        entity = InterviewMessage(**kwargs)
        session.add(entity)
        session.flush()
        return entity

    @staticmethod
    def list_by_session_id(session: Session, session_id: str) -> list[InterviewMessage]:
        stmt = (
            select(InterviewMessage)
            .where(InterviewMessage.session_id == session_id)
            .order_by(InterviewMessage.created_at, InterviewMessage.id)
        )
        return list(session.execute(stmt).scalars())

    @staticmethod
    def get_last_by_session_id(session: Session, session_id: str) -> InterviewMessage | None:
        stmt = (
            select(InterviewMessage)
            .where(InterviewMessage.session_id == session_id)
            .order_by(desc(InterviewMessage.created_at), desc(InterviewMessage.id))
            .limit(1)
        )
        return session.execute(stmt).scalar_one_or_none()
