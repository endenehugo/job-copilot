"""LLM 调用计量表：每次模型调用落一行。

为什么按「调用」而不是按「请求」记：一次 chat 请求会串起回答、压缩、校验、
重排多次调用，拆成多行才能在监控页按模型/按用途聚合；「请求数」这类请求级
口径由 trace_id 反推（COUNT(DISTINCT trace_id)），不重复落表。

失败调用也要记（error 非空、token 为空）：全局日预算统计的是"实际烧掉的
token"，降级链多试几个模型时消耗一样在发生，不能因为最终失败就不算。

表按天查询（配额、日报表），created_at 上的索引是查询主路径；
(owner_key, created_at) 复合索引服务"单访客今日次数"这一最高频配额查询。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.repositories.mysql_base import Base


class LlmUsage(Base):
    __tablename__ = "llm_usage"
    __table_args__ = (
        Index("idx_llm_usage_owner_time", "owner_key", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    # 归属：访客 id（v_xxx）/ admin。后台任务（压缩、补题）没有请求作用域，落空串
    owner_key: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    # 用途：取自请求级 trace 的 kind（chat / chat_stream / interview ...）；
    # 没有请求作用域时为 background
    purpose: Mapped[str] = mapped_column(String(32), nullable=False, default="background")
    in_tokens: Mapped[int] = mapped_column(Integer, nullable=True)
    out_tokens: Mapped[int] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error: Mapped[str] = mapped_column(String(64), nullable=True)
    trace_id: Mapped[str] = mapped_column(String(16), nullable=False, default="")


class LlmUsageRepository:
    def create(self, session: Session, **kwargs) -> LlmUsage:
        entity = LlmUsage(**kwargs)
        session.add(entity)
        session.flush()
        return entity

    @staticmethod
    def _sum_tokens_stmt(since: datetime):
        in_sum = func.coalesce(func.sum(LlmUsage.in_tokens), 0)
        out_sum = func.coalesce(func.sum(LlmUsage.out_tokens), 0)
        return select(in_sum + out_sum).where(LlmUsage.created_at >= since)

    def sum_tokens_since(self, session: Session, since: datetime) -> int:
        """该时间点之后烧掉的 token 总量（输入+输出，含失败调用已产出的部分）。"""
        return int(session.execute(self._sum_tokens_stmt(since)).scalar() or 0)

    def count_chat_traces(self, session: Session, owner_key: str, since: datetime) -> int:
        """某访客今天发起的 chat 请求数：按 trace_id 去重，而不是按调用行数。

        一次流式 chat 至少 2 行（回答 + 校验/压缩），按行数计会把限额除以 3。
        trace_id 为空串的行（无 trace 的调用）不参与计数。
        """
        stmt = (
            select(func.count(func.distinct(LlmUsage.trace_id)))
            .where(
                LlmUsage.owner_key == owner_key,
                LlmUsage.created_at >= since,
                LlmUsage.trace_id != "",
                LlmUsage.purpose.in_(("chat", "chat_stream")),
            )
        )
        return int(session.execute(stmt).scalar() or 0)

    def list_since(self, session: Session, since: datetime, cap: int = 8000) -> list[LlmUsage]:
        """取时间范围内的行做 Python 侧聚合（分位数、按用途分组）。

        有上限：监控页是管理员低频页面，宁可分位数截断在最近 cap 行，
        也不做无界查询；表按天看也就几千行，cap=8000 足够覆盖。
        """
        stmt = (
            select(LlmUsage)
            .where(LlmUsage.created_at >= since)
            .order_by(LlmUsage.id.desc())
            .limit(cap)
        )
        return list(session.execute(stmt).scalars())

    def list_recent(self, session: Session, limit: int = 100) -> list[LlmUsage]:
        stmt = select(LlmUsage).order_by(LlmUsage.id.desc()).limit(limit)
        return list(session.execute(stmt).scalars())

    def top_owners_since(self, session: Session, since: datetime, limit: int = 10) -> list[tuple[str, int, int]]:
        """用量最高的访客：(owner_key, 调用次数, token 合计)。滥用排查的入口。"""
        in_sum = func.coalesce(func.sum(LlmUsage.in_tokens), 0)
        out_sum = func.coalesce(func.sum(LlmUsage.out_tokens), 0)
        stmt = (
            select(
                LlmUsage.owner_key,
                func.count(LlmUsage.id),
                in_sum + out_sum,
            )
            .where(LlmUsage.created_at >= since)
            .group_by(LlmUsage.owner_key)
            .order_by((in_sum + out_sum).desc())
            .limit(limit)
        )
        return [(row[0], int(row[1]), int(row[2])) for row in session.execute(stmt)]
