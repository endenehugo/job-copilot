"""流式面试评价的回归测试。

覆盖三件事，都是这次改动里"错了也不报错、只会静默变差"的地方：

1. 半截 JSON 里取字段：模型逐字吐 JSON，取字段时必须处理未闭合字符串、
   跨 chunk 的转义序列（``\\n`` / ``\\uXXXX``）。取错不会抛异常，只会让
   用户看到缺字或乱码的评价。
2. delta 只能往前长：模型偶发重复输出同一段时，界面不能倒着擦字。
3. 解析失败必须抛错、不落库：把半截 JSON 猜成评价写进面试记录，
   比让用户重试一次严重得多。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessageChunk

from app.api import interviews
from app.services.interview_simulation_service import (
    InterviewSimulationService,
    extract_partial_string,
)


class TestExtractPartialString:
    def test_unterminated_value(self):
        assert extract_partial_string('{"evaluation": "回答结构清', "evaluation") == "回答结构清"

    def test_closed_value_stops_at_quote(self):
        assert extract_partial_string('{"evaluation": "好", "score": 80}', "evaluation") == "好"

    def test_empty_value_is_not_missing_field(self):
        # 空串和"还没开始写"要区分：前者是模型确实给了空评价
        assert extract_partial_string('{"evaluation": "", "score": 80}', "evaluation") == ""

    def test_escapes_are_decoded(self):
        assert extract_partial_string('{"evaluation": "a\\nb\\"c"', "evaluation") == 'a\nb"c'

    def test_half_escape_waits_for_next_chunk(self):
        # 末尾正好是 "\u4e2"：这一帧先停住，别把半个码点显示成怪字符
        assert extract_partial_string('{"evaluation": "尾巴\\u4e2', "evaluation") == "尾巴"
        assert extract_partial_string('{"evaluation": "尾巴\\u4e2d', "evaluation") == "尾巴中"

    def test_trailing_backslash_waits(self):
        assert extract_partial_string('{"evaluation": "换行前\\', "evaluation") == "换行前"

    def test_missing_or_non_string_value(self):
        assert extract_partial_string('{"score": 8', "evaluation") == ""
        assert extract_partial_string('{"evaluation": null}', "evaluation") == ""

    def test_similar_key_does_not_match(self):
        assert extract_partial_string('{"evaluation_note": "别命中"}', "evaluation") == ""


def _chunks(text: str, size: int) -> list[AIMessageChunk]:
    """按固定宽度切片，故意切在汉字与转义序列中间。"""
    return [AIMessageChunk(content=text[i : i + size]) for i in range(0, len(text), size)]


def _service_with_stream(chunks) -> InterviewSimulationService:
    svc = InterviewSimulationService()
    llm = MagicMock()
    llm.stream.return_value = iter(chunks)
    svc._ensure_answer_llm = lambda streaming=False: llm
    return svc


PAYLOAD = {
    "evaluation": "结构清晰，先讲了问题再讲改法；不足是缺少并发量的量化。",
    "score": 78,
    "next_action": "continue",
    "follow_up": "那幂等性怎么保证？",
    "overall_summary": None,
}


def test_stream_emits_growing_deltas_then_result():
    raw = json.dumps(PAYLOAD, ensure_ascii=False)
    svc = _service_with_stream(_chunks(raw, 7))

    events = list(svc.stream_evaluate_answer("jd", "resume", [], "当前题目", "我的回答", 1))
    deltas = [payload for kind, payload in events if kind == "delta"]

    assert deltas, "至少要有一个评价分片"
    assert deltas == sorted(deltas, key=len), "delta 只能往前长，不能回撤"
    assert deltas[-1] == PAYLOAD["evaluation"]

    kind, result = events[-1]
    assert kind == "result"
    assert result["score"] == 78
    assert result["follow_up"] == "那幂等性怎么保证？"


def test_stream_handles_list_content_chunks():
    """多模态模型会把 content 包成 [{'type': 'text', ...}]，拼接不能丢字。"""
    raw = json.dumps(PAYLOAD, ensure_ascii=False)
    chunks = [
        AIMessageChunk(content=[{"type": "text", "text": raw[i : i + 9]}])
        for i in range(0, len(raw), 9)
    ]
    svc = _service_with_stream(chunks)

    events = list(svc.stream_evaluate_answer("jd", "resume", [], "q", "a", 1))
    assert events[-1][1]["score"] == 78


def test_truncated_json_raises_instead_of_guessing():
    """max_tokens 截断时宁可报错让用户重试，也不落库半截评价。"""
    raw = json.dumps(PAYLOAD, ensure_ascii=False)[:60]
    svc = _service_with_stream(_chunks(raw, 5))

    with pytest.raises(ValueError):
        list(svc.stream_evaluate_answer("jd", "resume", [], "q", "a", 1))


def _patch_db(monkeypatch):
    """把落库替换成空实现，只验证轮次与返回结构。"""
    session = MagicMock()
    monkeypatch.setattr(
        interviews.DatabaseManager, "get_session", staticmethod(lambda: session)
    )
    monkeypatch.setattr(
        interviews.DatabaseManager, "remove_session", staticmethod(lambda: None)
    )
    monkeypatch.setattr(
        interviews.InterviewSessionRepository,
        "get_by_session_id",
        staticmethod(lambda _session, _sid: MagicMock()),
    )
    monkeypatch.setattr(
        interviews.InterviewSessionRepository, "update", staticmethod(lambda *a, **k: None)
    )
    monkeypatch.setattr(
        interviews.InterviewMessageRepository, "create", staticmethod(lambda *a, **k: None)
    )
    return session


def test_persist_answer_advances_question_index(monkeypatch):
    _patch_db(monkeypatch)
    warnings: list[str] = []

    data = interviews._persist_answer(
        session_id="iv_test",
        answer="我的回答",
        question_index=2,
        round_number=3,
        eval_result={
            "evaluation": "还行",
            "score": 78,
            "next_action": "continue",
            "follow_up": "下一题",
            "overall_summary": None,
        },
        eval_warnings=warnings,
    )

    assert data["action"] == "continue"
    assert data["question_index"] == 3
    assert data["next_question"] == "下一题"
    assert warnings == []


def test_persist_answer_forces_summary_at_round_limit(monkeypatch):
    """模型第 8 轮还说 continue 时，必须由服务端兜底结束。"""
    _patch_db(monkeypatch)
    warnings: list[str] = []

    data = interviews._persist_answer(
        session_id="iv_test",
        answer="我的回答",
        question_index=7,
        round_number=interviews.MAX_INTERVIEW_ROUNDS,
        eval_result={
            "evaluation": "还行",
            "score": 70,
            "next_action": "continue",
            "follow_up": "不该出现的下一题",
            "overall_summary": None,
        },
        eval_warnings=warnings,
    )

    assert data["action"] == "summary"
    assert "next_question" not in data
    assert data["total_rounds"] == interviews.MAX_INTERVIEW_ROUNDS
    assert any("已达最大轮数" in w for w in warnings)


def test_persist_answer_turns_empty_question_into_summary(monkeypatch):
    """B18 搬到这里：题库用尽、追问也没生成时，不能返回 continue + 空题面。"""
    _patch_db(monkeypatch)
    warnings: list[str] = []

    data = interviews._persist_answer(
        session_id="iv_test",
        answer="我的回答",
        question_index=1,
        round_number=2,
        eval_result={
            "evaluation": "还行",
            "score": 70,
            "next_action": "continue",
            "follow_up": None,
            "overall_summary": None,
        },
        eval_warnings=warnings,
        next_question="",
    )

    assert data["action"] == "summary"
    assert "next_question" not in data
    assert any("没有可用的下一题" in w for w in warnings)


def test_persist_answer_uses_given_next_question(monkeypatch):
    """题目由题库/追问给定时，模型在评价里带不带 follow_up 都不影响结果。"""
    _patch_db(monkeypatch)
    warnings: list[str] = []

    data = interviews._persist_answer(
        session_id="iv_test",
        answer="我的回答",
        question_index=0,
        round_number=1,
        eval_result={
            "evaluation": "还行",
            "score": 70,
            "next_action": "continue",
            "follow_up": "模型顺手写的下一题（应被忽略）",
            "overall_summary": None,
        },
        eval_warnings=warnings,
        next_question="题库里的第 2 题",
    )

    assert data["action"] == "continue"
    assert data["next_question"] == "题库里的第 2 题"
