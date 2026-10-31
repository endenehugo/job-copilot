"""llm_usage 计量落库与配额熔断的行为测试。

用 SQLite 内存库替换 MySQL（与 test_ownership_isolation 同一套打桩方式）：
- record_usage 走真实的 DatabaseManager.get_session 链路，验证条件事务与落库；
- 配额判断 monkeypatch 掉仓库层的计数/求和，聚焦阈值与豁免逻辑；
- 落库失败熔断用"让 create 抛异常"触发，验证业务不被计量挂掉。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import scoped_session, sessionmaker

from app.core import llm_usage
from app.core.config import settings
from app.core.llm_usage import check_chat_quota, day_start, record_usage
from app.core.ownership import request_owner_scope
from app.repositories.mysql_base import Base, DatabaseManager

_TEST_DB_URL = "sqlite:///file:llm_usage_test?mode=memory&cache=shared&uri=true"


@pytest.fixture(scope="module")
def engine():
    import app.repositories.llm_usage_repository  # noqa: F401  确保 LlmUsage 注册进 metadata

    engine = create_engine(_TEST_DB_URL, future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _patch_db(engine):
    factory = scoped_session(
        sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    )
    original_get = DatabaseManager.__dict__["get_session"]
    original_ensure = DatabaseManager.__dict__["ensure_tables"]
    original_remove = DatabaseManager.__dict__["remove_session"]

    DatabaseManager.get_session = classmethod(lambda cls: factory())
    DatabaseManager.ensure_tables = classmethod(lambda cls: None)
    DatabaseManager.remove_session = classmethod(lambda cls: factory.remove())
    try:
        yield
    finally:
        DatabaseManager.get_session = original_get
        DatabaseManager.ensure_tables = original_ensure
        DatabaseManager.remove_session = original_remove
        factory.remove()


@pytest.fixture(autouse=True)
def _reset_state(engine):
    """每个用例前后：清表 + 复位落库熔断器（模块级全局，串了会互相污染）。"""

    with engine.connect() as connection:
        connection.execute(llm_usage_table_delete())
        connection.commit()
    llm_usage._consecutive_failures = 0
    llm_usage._disabled_until = 0.0
    yield
    llm_usage._consecutive_failures = 0
    llm_usage._disabled_until = 0.0


@pytest.fixture(autouse=True)
def _usage_log_on(monkeypatch):
    """本文件要真跑计量落库：覆盖 conftest 的全局关闭。"""
    monkeypatch.setattr(settings, "llm_usage_log", True)


def llm_usage_table_delete():
    from app.repositories.llm_usage_repository import LlmUsage

    return LlmUsage.__table__.delete()


def _rows():
    from app.repositories.llm_usage_repository import LlmUsage

    session = DatabaseManager.get_session()
    try:
        return list(session.execute(select(LlmUsage)).scalars())
    finally:
        DatabaseManager.remove_session()


# ---------------------------------------------------------------------------
# record_usage：落库 + 作用域取值 + 失败不影响业务
# ---------------------------------------------------------------------------

def test_record_usage_writes_row_without_scope():
    """无请求作用域（后台线程视角）：owner 空、purpose=background、trace_id 空。"""
    record_usage(model="qwen3.8-max", duration_ms=123.4, in_tokens=10, out_tokens=5)
    rows = _rows()
    assert len(rows) == 1
    row = rows[0]
    assert row.owner_key == ""
    assert row.purpose == "background"
    assert row.trace_id == ""
    assert row.in_tokens == 10
    assert row.out_tokens == 5
    assert row.duration_ms == 123.4
    assert row.fallback is False
    assert row.error is None


def test_record_usage_picks_up_request_scope():
    """chat 链路视角：owner 与 trace 信息来自请求作用域的 ContextVar。"""
    from app.core.perf_trace import current_trace, trace_scope

    with request_owner_scope("v_abc123"):
        # request_owner_scope 只给了 owner；trace 仍是 None → purpose 落 background
        record_usage(model="qwen3.8-flash", duration_ms=1.0)
    row = _rows()[0]
    assert row.owner_key == "v_abc123"

    # 带 trace 时 purpose/trace_id 取自当前请求级计时
    with request_owner_scope("v_abc123"), trace_scope("chat_stream", conversation_id="c1") as trace:
        record_usage(model="qwen3.8-flash", duration_ms=2.0)
    rows = _rows()
    stream_row = [r for r in rows if r.purpose == "chat_stream"][0]
    assert stream_row.owner_key == "v_abc123"
    assert stream_row.trace_id == trace.trace_id
    assert current_trace() is None


def test_record_usage_disabled_by_settings(monkeypatch):
    monkeypatch.setattr(settings, "llm_usage_log", False)
    record_usage(model="m", duration_ms=1.0)
    assert _rows() == []


def test_record_usage_failure_never_raises(monkeypatch):
    """落库炸了也不能把 LLM 调用挂掉；连续 3 次失败后熔断停写。"""
    calls = {"n": 0}

    def boom(self, session, **kwargs):
        calls["n"] += 1
        raise RuntimeError("db down")

    monkeypatch.setattr("app.repositories.llm_usage_repository.LlmUsageRepository.create", boom)
    for _ in range(3):
        record_usage(model="m", duration_ms=1.0)  # 不应抛出
    assert calls["n"] == 3
    # 第 4 次被熔断短路：create 不再被调到
    record_usage(model="m", duration_ms=1.0)
    assert calls["n"] == 3


def test_record_usage_circuit_breaker_recovers():
    """熔断只停 5 分钟，之后自动恢复写库。"""
    llm_usage._disabled_until = 1.0  # 早已过期（monotonic 远大于 1.0）
    record_usage(model="m", duration_ms=1.0)
    assert len(_rows()) == 1


# ---------------------------------------------------------------------------
# check_chat_quota：阈值 / 豁免 / 查询失败放行
# ---------------------------------------------------------------------------

@pytest.fixture()
def quota_on(monkeypatch):
    monkeypatch.setattr(settings, "quota_enabled", True)
    monkeypatch.setattr(settings, "visitor_daily_chat_limit", 2)
    monkeypatch.setattr(settings, "global_daily_token_limit", 1000)


def test_quota_admin_exempt(quota_on):
    assert check_chat_quota(role="admin", owner_key="*") is None


def test_quota_disabled(quota_on, monkeypatch):
    monkeypatch.setattr(settings, "quota_enabled", False)
    assert check_chat_quota(role="user", owner_key="v_x") is None


def test_quota_no_identity_rejected(quota_on):
    """拿不到身份的请求按最保守处理：直接拒绝，而不是放行。"""
    result = check_chat_quota(role="user", owner_key="")
    assert result is not None and result[0] == 429


def test_quota_visitor_limit(quota_on, monkeypatch):
    from app.repositories.llm_usage_repository import LlmUsageRepository

    monkeypatch.setattr(LlmUsageRepository, "count_chat_traces", lambda self, s, o, since: 2)
    monkeypatch.setattr(LlmUsageRepository, "sum_tokens_since", lambda self, s, since: 0)
    result = check_chat_quota(role="user", owner_key="v_x")
    assert result is not None and result[0] == 429
    assert "今日体验次数已用完" in result[1]
    # 没到限额：放行
    monkeypatch.setattr(LlmUsageRepository, "count_chat_traces", lambda self, s, o, since: 1)
    assert check_chat_quota(role="user", owner_key="v_x") is None


def test_quota_global_token_limit(quota_on, monkeypatch):
    """单访客没超，但全站预算烧完也要挡住——访客身份只是个 cookie，换号零成本。"""
    from app.repositories.llm_usage_repository import LlmUsageRepository

    monkeypatch.setattr(LlmUsageRepository, "count_chat_traces", lambda self, s, o, since: 0)
    monkeypatch.setattr(LlmUsageRepository, "sum_tokens_since", lambda self, s, since: 1000)
    result = check_chat_quota(role="user", owner_key="v_x")
    assert result is not None and result[0] == 429
    assert "全站体验额度" in result[1]


def test_quota_query_failure_allows_request(quota_on, monkeypatch):
    """配额查询失败放行：计量故障不能打挂聊天。"""
    from app.repositories.llm_usage_repository import LlmUsageRepository

    def boom(self, session, owner_key, since):
        raise RuntimeError("db down")

    monkeypatch.setattr(LlmUsageRepository, "count_chat_traces", boom)
    assert check_chat_quota(role="user", owner_key="v_x") is None


# ---------------------------------------------------------------------------
# 聚合统计（监控页数据源）与 day_start
# ---------------------------------------------------------------------------

def test_day_start():
    now = datetime(2026, 10, 31, 8, 30)
    start = day_start(now)
    assert (start.hour, start.minute, start.second) == (0, 0, 0)
    assert now - start < timedelta(days=1)


def test_admin_summary_aggregates():
    from app.core.perf_trace import trace_scope

    with request_owner_scope("v_abc"), trace_scope("chat", conversation_id="c1"):
        record_usage(model="qwen3.8-max", duration_ms=100.0, in_tokens=100, out_tokens=50)
        record_usage(model="qwen3.8-max", duration_ms=300.0, in_tokens=10, out_tokens=5, error="ValueError")
        record_usage(model="qwen3.7-flash", duration_ms=50.0, fallback=True, in_tokens=1, out_tokens=1)

    summary = llm_usage.admin_summary(day_start())
    assert summary["total"]["llm_calls"] == 3
    assert summary["total"]["in_tokens"] == 111
    assert summary["total"]["out_tokens"] == 56
    assert summary["total"]["errors"] == 1
    assert summary["total"]["fallbacks"] == 1
    assert summary["total"]["chat_requests"] == 1
    models = {m["model"]: m for m in summary["by_model"]}
    assert models["qwen3.8-max"]["calls"] == 2
    assert models["qwen3.8-max"]["errors"] == 1
    purposes = {p["purpose"]: p for p in summary["by_purpose"]}
    assert purposes["chat"]["calls"] == 3
    assert purposes["chat"]["p95_ms"] >= purposes["chat"]["p50_ms"]
    owners = {o["owner_key"]: o for o in summary["top_owners"]}
    assert owners["v_abc"]["calls"] == 3
    assert summary["quota"]["visitor_daily_chat_limit"] == settings.visitor_daily_chat_limit


def test_recent_usage_shape():
    record_usage(model="m", duration_ms=1.0, in_tokens=2, out_tokens=3)
    recent = llm_usage.recent_usage(limit=10)
    assert len(recent) == 1
    item = recent[0]
    assert {"id", "time", "owner", "purpose", "model", "in_tokens", "out_tokens", "duration_ms"} <= set(item)
