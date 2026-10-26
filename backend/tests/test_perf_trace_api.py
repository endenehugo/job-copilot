"""chat 接口的请求级计时：响应头开关、日志、流式 ttft/done、对原有响应零影响。"""

from __future__ import annotations

import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import conversations
from app.core import perf_trace as pt
from app.core.config import settings
from app.core.ownership import get_owner_key
from app.core.security import get_current_role

URL = "/api/v1/conversation/chat"
STREAM_URL = "/api/v1/conversation/chat/stream"
PAYLOAD = {"conversation_id": "c1", "query": "你好，这是一个秘密问题", "mode": "memory"}


class FakeChatService:
    """按真实服务的方式打点，但不依赖任何模型或数据库。"""

    def __init__(self, fail: Exception | None = None):
        self.fail = fail

    def chat(self, conversation_id, query, mode, image_urls, skill=""):
        if self.fail is not None:
            raise self.fail
        with pt.span("db.history"):
            pass
        with pt.span("answer"):
            pt.record_llm_call("m", 3.0)
        return {"answer": "答案", "original_answer": "答案", "conversation": {}, "sources": [], "files": []}

    def chat_stream(self, conversation_id, query, mode, image_urls, skill=""):
        with pt.span("db.history"):
            pass
        yield {"event": "meta", "data": {"conversation_id": conversation_id}}
        yield {"event": "delta", "data": {"text": "答"}}
        yield {"event": "done", "data": {"answer": "答案"}}


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(conversations.router)
    app.dependency_overrides[get_current_role] = lambda: "user"
    app.dependency_overrides[get_owner_key] = lambda: "v_test"
    service = FakeChatService()
    monkeypatch.setattr(conversations, "get_conversation_chat_service", lambda: service)
    test_client = TestClient(app)
    test_client.service = service
    return test_client


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.setattr(settings, "perf_trace_header", False, raising=False)
    monkeypatch.setattr(settings, "perf_trace_log", True, raising=False)


def _perf_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "app.perf"]


def test_header_hidden_when_switch_off(client):
    resp = client.post(URL, json=PAYLOAD, headers={"X-Perf-Trace": "1"})
    assert resp.status_code == 200 and "server-timing" not in resp.headers


def test_header_needs_switch_and_request_header(client, monkeypatch):
    monkeypatch.setattr(settings, "perf_trace_header", True, raising=False)
    assert "server-timing" not in client.post(URL, json=PAYLOAD).headers
    assert "server-timing" not in client.post(URL, json=PAYLOAD, headers={"X-Perf-Trace": "0"}).headers
    resp = client.post(URL, json=PAYLOAD, headers={"X-Perf-Trace": "1"})
    header = resp.headers["server-timing"]
    assert header.startswith("total;dur=")
    assert "db;dur=" in header and "answer;dur=" in header
    assert 'llm;dur=3.0;desc="n=1"' in header


def test_body_unchanged_by_tracing(client, monkeypatch):
    base = client.post(URL, json=PAYLOAD).json()
    monkeypatch.setattr(settings, "perf_trace_header", True, raising=False)
    traced = client.post(URL, json=PAYLOAD, headers={"X-Perf-Trace": "1"}).json()
    assert base == traced
    assert base["code"] == "success" and base["data"]["answer"] == "答案"


def test_one_perf_log_per_chat_without_user_text(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.perf"):
        client.post(URL, json=PAYLOAD)
    lines = _perf_lines(caplog)
    assert len(lines) == 1
    assert "秘密" not in lines[0] and "答案" not in lines[0]
    data = json.loads(lines[0][5:])
    assert data["endpoint"] == "chat" and data["conversation_id"] == "c1" and data["mode"] == "memory"
    assert data["llm_calls"] == 1


def test_validation_errors_do_not_emit_perf_log(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.perf"):
        body = client.post(URL, json={**PAYLOAD, "conversation_id": ""}).json()
    assert body["code"] == 400
    assert _perf_lines(caplog) == []


def test_service_value_error_keeps_400_envelope_and_no_header(client, monkeypatch):
    monkeypatch.setattr(settings, "perf_trace_header", True, raising=False)
    client.service.fail = ValueError("会话不存在")
    resp = client.post(URL, json=PAYLOAD, headers={"X-Perf-Trace": "1"})
    assert resp.json()["code"] == 400 and resp.json()["message"] == "会话不存在"
    assert "server-timing" not in resp.headers


def test_perf_log_can_be_switched_off(client, monkeypatch, caplog):
    monkeypatch.setattr(settings, "perf_trace_log", False, raising=False)
    with caplog.at_level(logging.INFO, logger="app.perf"):
        client.post(URL, json=PAYLOAD)
    assert _perf_lines(caplog) == []


def _sse_events(text):
    events = []
    for block in text.strip().split("\n\n"):
        name = block.split("\n", 1)[0].removeprefix("event: ")
        events.append(name)
    return events


def test_stream_logs_ttft_and_done_and_keeps_events(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.perf"):
        resp = client.post(STREAM_URL, json=PAYLOAD)
    assert _sse_events(resp.text) == ["meta", "delta", "done"]
    lines = _perf_lines(caplog)
    assert len(lines) == 1
    data = json.loads(lines[0][5:])
    assert data["endpoint"] == "chat_stream"
    assert 0 <= data["ttft_ms"] <= data["done_ms"] <= data["total_ms"]
    assert "server-timing" not in resp.headers
