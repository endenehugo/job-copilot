"""LLM 调用计量与配额熔断。

三件事收在一个模块里，因为它们共用同一张 llm_usage 表和同一套
"读当前请求作用域"的取值方式：

1. record_usage：把每次 LLM 调用（模型/token/耗时/是否降级/错误）落一行。
   数据源是 llm_factory._record_llm——全仓所有模型调用都从工厂过，这里是唯一埋点。
2. check_chat_quota：聊天入口的每日配额熔断（展示站防滥用）。
   免费额度按模型独立发放且不可恢复，公网上没有这层的话，一个循环脚本
   一晚上就能把整条降级链的额度烧穿——演示站会在最需要它的那天挂掉。
3. admin_summary / recent_usage：管理员监控页的聚合数据。

设计约束：
- 计量绝不能影响业务：落库失败只告警；连续失败后熔断停写 5 分钟，
  避免 DB 不可用时每个 LLM 调用都白等一次连接超时。
- 落库沿用 generated_file_service 的条件事务模式（scoped_session 是线程内
  共享的，调用方可能开着事务；不能 close / rollback 外层的事务）。
- 配额按"自然日"（本地时区）重置，与免费额度"按量不按天"的规则无关——
  这里防的是滥用，不是记账精确性。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, time as dt_time

from app.core.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 落库熔断器：连续失败 N 次后停写一段时间，期间 record_usage 直接返回。
# 只防"DB 不可用时的雪上加霜"，正常情况下永远不会触发。
# ---------------------------------------------------------------------------
_FAILURE_LOCK = threading.Lock()
_consecutive_failures = 0
_disabled_until = 0.0
_FAILURE_THRESHOLD = 3
_RETRY_AFTER_SECONDS = 300


def _record_allowed() -> bool:
    now = time.monotonic()
    with _FAILURE_LOCK:
        return now >= _disabled_until


def _mark_success() -> None:
    global _consecutive_failures
    with _FAILURE_LOCK:
        _consecutive_failures = 0


def _mark_failure() -> None:
    global _consecutive_failures, _disabled_until
    with _FAILURE_LOCK:
        _consecutive_failures += 1
        if _consecutive_failures == _FAILURE_THRESHOLD:
            _disabled_until = time.monotonic() + _RETRY_AFTER_SECONDS
            logger.warning(
                "llm_usage 连续 %d 次落库失败，停写 %d 秒后自动恢复",
                _FAILURE_THRESHOLD,
                _RETRY_AFTER_SECONDS,
            )


def day_start(now: datetime | None = None) -> datetime:
    """今天零点（本地时区）。配额与日报表的时间基准。"""
    ref = now or datetime.now()
    return datetime.combine(ref.date(), dt_time.min)


def record_usage(
    *,
    model: str,
    duration_ms: float,
    in_tokens: int | None = None,
    out_tokens: int | None = None,
    fallback: bool = False,
    error: str | None = None,
) -> None:
    """记录一次 LLM 调用。任何异常都不允许传播——计量挂了不能挂业务。"""
    if not settings.llm_usage_log or not _record_allowed():
        return
    # 用途与归属从请求作用域取：chat 链路里 LLM 调用发生在 DB 事务之外，
    # 这两个 ContextVar 都已就位；后台线程（压缩/补题）取到空串 → background
    from app.core import perf_trace
    from app.core.ownership import current_owner_key

    trace = perf_trace.current_trace()
    row = {
        "created_at": datetime.now(),
        "owner_key": current_owner_key() or "",
        "model": model,
        "purpose": trace.kind if trace is not None else "background",
        "in_tokens": in_tokens,
        "out_tokens": out_tokens,
        "duration_ms": round(duration_ms, 1),
        "fallback": bool(fallback),
        "error": error,
        "trace_id": trace.trace_id if trace is not None else "",
    }
    # 延迟导入：repositories 依赖 mysql_base 的引擎惰性初始化，模块加载期不动它
    from app.repositories import DatabaseManager
    from app.repositories.llm_usage_repository import LlmUsageRepository

    session = DatabaseManager.get_session()
    try:
        if session.in_transaction():
            # 外层有事务（少见，但 register_generated_file 踩过）：挂到外层，
            # 只 flush 不 commit——commit 的时机归调用方
            LlmUsageRepository().create(session, **row)
        else:
            with session.begin():
                LlmUsageRepository().create(session, **row)
        _mark_success()
    except Exception as exc:
        _mark_failure()
        logger.warning("llm_usage 落库失败（不影响业务）：%s", exc)
    finally:
        DatabaseManager.remove_session()


# ---------------------------------------------------------------------------
# 配额熔断：聊天入口（chat / chat_stream）在生成前调用。
# 返回 None 表示放行；返回 (code, message) 时入口直接以统一信封拒绝。
# ---------------------------------------------------------------------------

def check_chat_quota(role: str, owner_key: str) -> tuple[int, str] | None:
    """访客每日聊天配额 + 全站每日 token 预算。

    - 管理员豁免：监控/演示自己的产品不该被自己挡住；
    - 未配置 AUTH_SECRET 的部署里 role 恒为 user，照常受限（fail safe）；
    - 两个阈值任一为 0 表示该维度不设限。
    """
    if not settings.quota_enabled or role == "admin":
        return None
    if not owner_key:
        # 没有身份的请求（中间件异常等）按最保守处理：直接拒绝
        return 429, "身份校验失败，请刷新页面后重试"

    since = day_start()
    from app.repositories import DatabaseManager
    from app.repositories.llm_usage_repository import LlmUsageRepository

    session = DatabaseManager.get_session()
    try:
        repo = LlmUsageRepository()
        if settings.visitor_daily_chat_limit > 0:
            chats = repo.count_chat_traces(session, owner_key, since)
            if chats >= settings.visitor_daily_chat_limit:
                return (
                    429,
                    f"今日体验次数已用完（每天 {settings.visitor_daily_chat_limit} 次），"
                    "明天再来吧～",
                )
        if settings.global_daily_token_limit > 0:
            tokens = repo.sum_tokens_since(session, since)
            if tokens >= settings.global_daily_token_limit:
                return 429, "今日全站体验额度已用完，明天再来吧～"
        return None
    except Exception as exc:
        # 配额查询失败时放行：宁可多烧一点额度，也不能让计量故障打挂聊天
        logger.warning("配额查询失败，本次放行：%s", exc)
        return None
    finally:
        DatabaseManager.remove_session()


# ---------------------------------------------------------------------------
# 管理员监控页的数据聚合。分位数在 Python 侧算：行数按天看是几百到几千，
# 拉 bounded 行（list_since cap=8000）在内存里算，比 MySQL 8 窗口函数兼容性好。
# ---------------------------------------------------------------------------

def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return round(ordered[idx], 1)


def admin_summary(since: datetime | None = None) -> dict:
    """时间范围内的总量、按模型、按用途、配额水位、用量 Top 访客。"""
    start = since or day_start()
    from app.repositories import DatabaseManager
    from app.repositories.llm_usage_repository import LlmUsageRepository

    session = DatabaseManager.get_session()
    try:
        repo = LlmUsageRepository()
        rows = repo.list_since(session, start)
        top_owners = repo.top_owners_since(session, start)
    finally:
        DatabaseManager.remove_session()

    total_in = sum(r.in_tokens or 0 for r in rows)
    total_out = sum(r.out_tokens or 0 for r in rows)
    errors = [r for r in rows if r.error]
    fallbacks = [r for r in rows if r.fallback]
    chat_traces = {r.trace_id for r in rows if r.purpose in ("chat", "chat_stream") and r.trace_id}

    by_model: dict[str, dict] = {}
    by_purpose: dict[str, list[float]] = {}
    for r in rows:
        entry = by_model.setdefault(
            r.model, {"model": r.model, "calls": 0, "in_tokens": 0, "out_tokens": 0, "errors": 0}
        )
        entry["calls"] += 1
        entry["in_tokens"] += r.in_tokens or 0
        entry["out_tokens"] += r.out_tokens or 0
        entry["errors"] += 1 if r.error else 0
        by_purpose.setdefault(r.purpose, []).append(r.duration_ms or 0.0)

    return {
        "since": start.strftime("%Y-%m-%d %H:%M:%S"),
        "rows": len(rows),
        "total": {
            "llm_calls": len(rows),
            "in_tokens": total_in,
            "out_tokens": total_out,
            "errors": len(errors),
            "fallbacks": len(fallbacks),
            "chat_requests": len(chat_traces),
        },
        "by_model": sorted(by_model.values(), key=lambda item: -item["calls"]),
        "by_purpose": [
            {
                "purpose": name,
                "calls": len(durations),
                "p50_ms": _percentile(durations, 0.5),
                "p95_ms": _percentile(durations, 0.95),
            }
            for name, durations in sorted(by_purpose.items())
        ],
        "top_owners": [
            {"owner_key": owner or "(后台任务)", "calls": calls, "tokens": tokens}
            for owner, calls, tokens in top_owners
        ],
        "quota": {
            "visitor_daily_chat_limit": settings.visitor_daily_chat_limit,
            "global_daily_token_limit": settings.global_daily_token_limit,
            "tokens_today": repo_sum_tokens_today(),
        },
    }


def repo_sum_tokens_today() -> int:
    """今天已烧的 token（全站口径），给配额水位展示用。查询失败返回 -1（前端显示 --）。"""
    from app.repositories import DatabaseManager
    from app.repositories.llm_usage_repository import LlmUsageRepository

    session = DatabaseManager.get_session()
    try:
        return LlmUsageRepository().sum_tokens_since(session, day_start())
    except Exception:
        return -1
    finally:
        DatabaseManager.remove_session()


def recent_usage(limit: int = 100) -> list[dict]:
    """最近 N 次调用，监控页的明细表。"""
    from app.repositories import DatabaseManager
    from app.repositories.llm_usage_repository import LlmUsageRepository

    session = DatabaseManager.get_session()
    try:
        rows = LlmUsageRepository().list_recent(session, limit)
    finally:
        DatabaseManager.remove_session()
    return [
        {
            "id": r.id,
            "time": r.created_at.strftime("%m-%d %H:%M:%S"),
            "owner": r.owner_key or "(后台)",
            "purpose": r.purpose,
            "model": r.model,
            "in_tokens": r.in_tokens,
            "out_tokens": r.out_tokens,
            "duration_ms": r.duration_ms,
            "fallback": r.fallback,
            "error": r.error,
        }
        for r in rows
    ]


__all__ = [
    "record_usage",
    "check_chat_quota",
    "admin_summary",
    "recent_usage",
    "day_start",
]
