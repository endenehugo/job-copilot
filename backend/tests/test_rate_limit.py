"""限流中间件的行为测试。

不拉起整个应用：搭一个只挂 RateLimitMiddleware 的最小 FastAPI，
路由路径对齐 _RULES 里的前缀，让桶匹配逻辑跑真实分支。
每个用例新建 app——中间件的计数表在实例里，天然隔离。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.rate_limit import RateLimitMiddleware
from app.core.security import create_token


def _app() -> TestClient:
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.post("/api/v1/auth/login")
    def login():
        return {"code": "success"}

    @app.post("/api/v1/conversation/chat")
    def chat():
        return {"code": "success"}

    @app.post("/api/v1/conversation/chat/stream")
    def chat_stream():
        return {"code": "success"}

    @app.post("/api/v1/document/upload")
    def upload():
        return {"code": "success"}

    @app.get("/api/v1/conversation/list")
    def listing():
        return {"code": "success"}

    @app.get("/api/v1/health-free")
    def free():
        return {"code": "success"}

    return TestClient(app)


@pytest.fixture(autouse=True)
def tight_limits(monkeypatch):
    """把所有限收紧到 2 次，方便用第 3 次请求触发。"""
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_ai_per_minute", 2)
    monkeypatch.setattr(settings, "rate_limit_ai_per_hour", 100)
    monkeypatch.setattr(settings, "rate_limit_upload_per_hour", 2)
    monkeypatch.setattr(settings, "rate_limit_login_per_10min", 2)


def test_ai_bucket_blocks_third_request():
    client = _app()
    for _ in range(2):
        assert client.post("/api/v1/conversation/chat", json={}).status_code == 200
    resp = client.post("/api/v1/conversation/chat", json={})
    assert resp.status_code == 429
    assert resp.json()["code"] == 429
    assert "太频繁" in resp.json()["message"]
    assert resp.headers.get("retry-after")


def test_prefix_covers_stream_variant():
    """前缀匹配：/chat/stream 与 /chat 共享同一个桶。"""
    client = _app()
    assert client.post("/api/v1/conversation/chat", json={}).status_code == 200
    assert client.post("/api/v1/conversation/chat/stream", json={}).status_code == 200
    assert client.post("/api/v1/conversation/chat", json={}).status_code == 429


def test_get_requests_not_counted():
    """AI 桶只数 POST：GET 列表/详情随便刷不受限。"""
    client = _app()
    for _ in range(5):
        assert client.get("/api/v1/conversation/list").status_code == 200
    assert client.get("/api/v1/health-free").status_code == 200


def test_buckets_are_independent():
    """upload 与 ai 各自计数，互不挤占。"""
    client = _app()
    for _ in range(2):
        assert client.post("/api/v1/document/upload").status_code == 200
    assert client.post("/api/v1/document/upload").status_code == 429
    # upload 打满了，chat 还有自己的额度
    assert client.post("/api/v1/conversation/chat", json={}).status_code == 200


def test_admin_exempt():
    client = _app()
    token = create_token("tester")
    for _ in range(5):
        resp = client.post(
            "/api/v1/conversation/chat", json={}, headers={"X-Admin-Token": token}
        )
        assert resp.status_code == 200


def test_login_bucket_and_identity_by_ip():
    client = _app()
    for _ in range(2):
        assert client.post("/api/v1/auth/login", json={}).status_code == 200
    resp = client.post("/api/v1/auth/login", json={})
    assert resp.status_code == 429
    assert "10 分钟" in resp.json()["message"]


def test_disabled_switch(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    client = _app()
    for _ in range(6):
        assert client.post("/api/v1/conversation/chat", json={}).status_code == 200


def test_zero_limit_disables_bucket(monkeypatch):
    """窗口阈值设 0 = 该桶关闭（语义：不限而不是全拒）。"""
    monkeypatch.setattr(settings, "rate_limit_ai_per_minute", 0)
    monkeypatch.setattr(settings, "rate_limit_ai_per_hour", 0)
    client = _app()
    for _ in range(5):
        assert client.post("/api/v1/conversation/chat", json={}).status_code == 200
