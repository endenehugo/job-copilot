"""管理员监控接口：LLM 计量聚合与最近调用明细。

数据源是 llm_usage 表（core/llm_usage.py 在 llm_factory 埋点落库），
本文件只做"鉴权 + 取数"的薄编排；聚合逻辑留在 core 层，测试可以直接打。
所有接口挂 require_admin：计量数据里有归属（owner_key）与用量画像，
等同用户隐私，不能裸奔在公网上。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query

from app.core.llm_usage import admin_summary, recent_usage
from app.core.response import ok
from app.core.security import require_admin

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/stats/summary")
def stats_summary(
    hours: float = Query(default=24, gt=0, le=24 * 30),
    _role: str = Depends(require_admin),
) -> dict:
    """时间范围内的总量 / 按模型 / 按用途（P50/P95）/ 配额水位 / Top 访客。"""
    since = datetime.now() - timedelta(hours=hours)
    return ok(admin_summary(since))


@router.get("/stats/usage/recent")
def stats_recent(
    limit: int = Query(default=100, ge=1, le=500),
    _role: str = Depends(require_admin),
) -> dict:
    """最近 N 次模型调用的明细表（监控页的滚动列表）。"""
    return ok({"items": recent_usage(limit)})
