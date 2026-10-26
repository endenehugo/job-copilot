"""历史压缩策略：触发阈值与目标水位分开，以及压缩调用的长度约束。

2026-10-26 线上实测（tools/loadtest/reports/20261026/REPORT-20261026-perf-trace.md）：
旧的单阈值逻辑每次只压掉溢出的 2 条，于是第 8 次请求之后每轮都触发一次额外模型调用，
压缩本身均值 4475ms、输出 142~383 token，比回答调用还慢一倍。这里覆盖修好后的行为。
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services.context_compression_service import ContextCompressionService


def _messages(count: int, start_id: int = 1) -> list[dict]:
    """按一问一答成对生成消息。"""
    roles = ("user", "assistant")
    return [
        {"id": start_id + i, "role": roles[i % 2], "content": f"消息{start_id + i}"}
        for i in range(count)
    ]


@pytest.fixture
def svc():
    service = ContextCompressionService()
    # 不碰网络：摘要合并直接返回固定文本
    service._merge_summary = lambda old_summary, new_messages: "摘要"
    return service


class TestTriggerAndTarget:
    def test_no_compress_at_threshold(self, svc) -> None:
        history = _messages(12)
        result, summary, watermark = svc.compress_history(
            history, old_summary="", max_messages=12, target_messages=8
        )
        assert watermark == 0 and summary == ""
        assert len(result) == 12

    def test_one_pass_drops_to_target(self, svc) -> None:
        history = _messages(14)
        result, summary, watermark = svc.compress_history(
            history, old_summary="", max_messages=12, target_messages=8
        )
        # 14 - 8 = 6 条被压掉，水位停在第 6 条上（assistant，成对）
        assert watermark == 6
        assert len(result) == 1 + 8
        assert result[0]["role"] == "system" and summary == "摘要"
        assert result[-1]["id"] == 14

    def test_odd_overflow_is_rounded_down_to_keep_pairs(self, svc) -> None:
        """压奇数条会让水位停在 user 上，下一轮 recent 从 assistant 开始。"""
        history = _messages(13)
        result, _, watermark = svc.compress_history(
            history, old_summary="", max_messages=12, target_messages=8
        )
        assert watermark == 4  # 溢出 5 条 → 压 4 条
        assert len(result) == 1 + 9

    def test_target_none_keeps_legacy_behaviour(self, svc) -> None:
        history = _messages(14)
        _, _, watermark = svc.compress_history(history, old_summary="", max_messages=12)
        assert watermark == 2


class TestTriggerFrequency:
    def test_compresses_once_every_three_turns(self, svc) -> None:
        """攒到 12 条才压、一次压到 8 条 → 每 3 轮触发一次（旧逻辑是每轮）。"""
        trigger, target = 12, 8
        history = _messages(2)
        summary = ""
        fired: list[int] = []
        next_id = 3
        for turn in range(2, 21):
            result, new_summary, watermark = svc.compress_history(
                history, old_summary=summary, max_messages=trigger, target_messages=target
            )
            if watermark:
                fired.append(turn)
                summary = new_summary
                history = result[1:]  # 摘要消息不进 DB，下一轮只读水位之后的消息
            history = history + _messages(2, start_id=next_id)
            next_id += 2
        assert fired == [8, 11, 14, 17, 20]


class TestSummaryLength:
    def test_compressor_llm_is_built_with_length_limits(self, monkeypatch) -> None:
        captured: dict = {}

        def fake_build_chat_llm(**kwargs):
            captured.update(kwargs)
            return object()

        monkeypatch.setattr("app.core.llm_factory.build_chat_llm", fake_build_chat_llm)
        service = ContextCompressionService()
        service._get_llm()
        assert captured["max_tokens"] == settings.history_summary_max_tokens
        assert captured["model"] == settings.llm_small_model
        # 开着思考时思考 token 也计入 max_tokens，实测会吃满额度、正文返回空串
        assert captured["enable_thinking"] is False

    def test_summary_is_capped_in_characters(self) -> None:
        class _LongLLM:
            def invoke(self, prompt):
                class _R:
                    content = "很长的摘要" * 500

                return _R()

        # 这个用例要真的走 _merge_summary，所以不用 svc fixture（它把合并函数替换掉了）
        service = ContextCompressionService()
        service._compressor_llm = _LongLLM()
        result, summary, _ = service.compress_history(
            _messages(14), old_summary="", max_messages=12, target_messages=8
        )
        assert len(summary) == ContextCompressionService._SUMMARY_MAX_CHARS
        assert len(result) == 1 + 8

    def test_prompt_asks_for_bounded_bullets(self) -> None:
        seen: dict = {}

        class _SpyLLM:
            def invoke(self, prompt):
                seen["prompt"] = prompt

                class _R:
                    content = "1. 用户在做求职项目"

                return _R()

        service = ContextCompressionService()
        service._compressor_llm = _SpyLLM()
        service._merge_summary("旧摘要", _messages(4))
        prompt = seen["prompt"]
        assert "最多 5 条" in prompt and "每条不超过 30 字" in prompt
        assert "旧摘要" in prompt and "消息1" in prompt
