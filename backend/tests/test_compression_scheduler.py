"""后台压缩调度：去重、写库、失败降级。

任务跑在线程池里，用例必须等待完成；用 pending_count() 轮询而不是 sleep 固定时长，
避免机器慢时偶发失败。
"""

from __future__ import annotations

import threading
import time

import pytest

from app.services import context_compression_scheduler as scheduler


def _drain(timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while scheduler.pending_count() and time.time() < deadline:
        time.sleep(0.01)
    assert scheduler.pending_count() == 0, "后台任务超时未结束"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    scheduler.reset_for_tests()
    yield
    scheduler.reset_for_tests()


class _FakeStore:
    def __init__(self, summary: str = "", until_id: int = 0, messages: int = 14):
        self.summary = summary
        self.until_id = until_id
        self.messages = messages
        self.saved: list[tuple] = []

    def get_conversation_summary(self, conversation_id):
        return self.summary, self.until_id

    def get_messages_after(self, conversation_id, until_id):
        return [{"id": i + 1, "role": "user", "content": "x"} for i in range(self.messages)]

    def save_conversation_summary(self, conversation_id, summary, until_id):
        self.saved.append((conversation_id, summary, until_id))


class _FakeCompression:
    def __init__(self, summary: str = "新摘要", watermark: int = 6, error: Exception | None = None):
        self.summary = summary
        self.watermark = watermark
        self.error = error
        self.calls: list[int] = []

    def compress_history(self, history, old_summary="", max_messages=20, target_messages=None):
        self.calls.append(len(history))
        if self.error is not None:
            raise self.error
        return ([], self.summary, self.watermark)


def _patch_services(monkeypatch, store, compression):
    import app.core.deps as deps

    monkeypatch.setattr(deps, "get_conversation_store_service", lambda: store)
    monkeypatch.setattr(deps, "get_context_compression_service", lambda: compression)


class TestSchedule:
    def test_writes_summary_when_watermark_moves(self, monkeypatch) -> None:
        store, compression = _FakeStore(), _FakeCompression(watermark=6)
        _patch_services(monkeypatch, store, compression)
        assert scheduler.schedule("c1") is True
        _drain()
        assert store.saved == [("c1", "新摘要", 6)]
        assert compression.calls == [14]

    def test_skips_write_when_nothing_to_compress(self, monkeypatch) -> None:
        store, compression = _FakeStore(messages=10), _FakeCompression(watermark=6)
        _patch_services(monkeypatch, store, compression)
        scheduler.schedule("c1")
        _drain()
        assert store.saved == [] and compression.calls == []

    def test_failure_does_not_write_or_raise(self, monkeypatch) -> None:
        store = _FakeStore()
        compression = _FakeCompression(error=RuntimeError("模型挂了"))
        _patch_services(monkeypatch, store, compression)
        assert scheduler.schedule("c1") is True
        _drain()
        assert store.saved == []

    def test_duplicate_schedule_is_ignored(self, monkeypatch) -> None:
        """同一会话排队中就不再提交，避免用户连点把队列塞满。"""
        gate = threading.Event()
        store, compression = _FakeStore(), _FakeCompression(watermark=6)
        original = compression.compress_history

        def slow(*args, **kwargs):
            gate.wait(2.0)  # 卡住第一个任务，模拟压缩还没跑完
            return original(*args, **kwargs)

        compression.compress_history = slow
        _patch_services(monkeypatch, store, compression)
        assert scheduler.schedule("c1") is True
        assert scheduler.schedule("c1") is False  # 还在排队
        assert scheduler.pending_count() == 1
        gate.set()
        _drain()
        # 跑完之后可以再次排队
        assert scheduler.schedule("c1") is True
        _drain()
        assert len(compression.calls) == 2

    def test_empty_conversation_id_is_ignored(self) -> None:
        assert scheduler.schedule("") is False
        assert scheduler.pending_count() == 0
