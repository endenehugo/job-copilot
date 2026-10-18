"""生成简历的持久化记录。

与 `resume_versions` 的区别：那张表是「上传简历 → 打分」的版本线（每条记录对应一次评分），
本表是「会话内素材 → 生成 A4 简历」的版本线（每条记录对应一份可打开的成品）。
两者语义不同，刻意不复用。

`data_json` 是唯一真相，HTML 只是派生物：文件被清理后可用 JSON 原样重渲染（0 次 LLM）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, desc, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.repositories.mysql_base import Base


class ResumeGeneration(Base):
    __tablename__ = "resume_generations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    generation_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ready")
    target_role: Mapped[str] = mapped_column(String(128), nullable=False, default="")

    # ResumeData 全量 JSON。实测 6–10KB，MySQL TEXT(64KB) 足够；
    # 换成超长简历也只会是几十 KB，不需要 LONGTEXT。
    data_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    missing_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    stages_json: Mapped[str] = mapped_column(String(255), nullable=False, default="{}")

    model: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    html_filename: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    html_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 同会话内的第几版。重新生成 = 新增一版而不是覆盖，用户要能回看上一版怎么写的。
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ResumeGenerationRepository:
    @staticmethod
    def create(session: Session, **kwargs) -> ResumeGeneration:
        entity = ResumeGeneration(**kwargs)
        session.add(entity)
        session.flush()
        return entity

    @staticmethod
    def get_by_generation_id(session: Session, generation_id: str) -> ResumeGeneration | None:
        stmt = select(ResumeGeneration).where(ResumeGeneration.generation_id == generation_id)
        return session.execute(stmt).scalar_one_or_none()

    @staticmethod
    def list_by_conversation_id(
        session: Session, conversation_id: str, limit: int = 50
    ) -> list[ResumeGeneration]:
        stmt = (
            select(ResumeGeneration)
            .where(ResumeGeneration.conversation_id == conversation_id)
            .order_by(desc(ResumeGeneration.id))
            .limit(limit)
        )
        return list(session.execute(stmt).scalars())

    @staticmethod
    def next_version(session: Session, conversation_id: str) -> int:
        stmt = (
            select(ResumeGeneration.version)
            .where(ResumeGeneration.conversation_id == conversation_id)
            .order_by(desc(ResumeGeneration.version))
            .limit(1)
        )
        latest = session.execute(stmt).scalar_one_or_none()
        return int(latest or 0) + 1
