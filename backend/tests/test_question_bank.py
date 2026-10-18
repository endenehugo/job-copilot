"""P2 题库化的单元测试。

覆盖三处"错了不会报错、只会静默变差"的地方：

1. ``initial_questions`` 的三种历史形态都要能解析（空值 / P1 的纯 list / P2 的包装）。
   解析错了不会抛异常，只会让老场次重问一遍问过的题，或者新场次丢掉整份题单。
2. 游标写坏比丢题更危险：越界的 cursor 会静默重复出题，必须退化成"已全部出过"。
3. 下一题的决策顺序（低分追问 → 题库 → 现场生成 → 模型自带）：走错分支时
   用户看到的是"题重复了"或"答崩了还换新题"，两种都不会报错。
"""

from __future__ import annotations

from app.services.question_bank import (
    FOLLOW_UP_SCORE_THRESHOLD,
    SHORT_ANSWER_CHARS,
    QuestionBank,
    dump_bank,
    needs_follow_up,
    parse_bank,
    plan_next_question,
)

LONG_ANSWER = "我在项目里把 LLM 调用从数据库事务里拆了出来，P95 从 46 秒降到 12 秒。" * 2


def _bank(*texts: str, cursor: int = 0, state: str = "ready") -> QuestionBank:
    return QuestionBank([{"question": t} for t in texts], cursor, state)


class TestParseBank:
    def test_empty_value(self):
        bank = parse_bank(None)
        assert bank.questions == [] and bank.cursor == 0 and bank.state == "ready"

    def test_legacy_plain_list_is_treated_as_exhausted(self):
        """P1 及更早写的是纯 list：不能重问，必须走追问兜底。"""
        bank = parse_bank('[{"question": "老题"}]')
        assert len(bank.questions) == 1
        assert bank.cursor == 1
        assert bank.next_question_text == ""

    def test_v2_wrapper_roundtrip(self):
        original = _bank("q1", "q2", "q3", cursor=1, state="pending")
        parsed = parse_bank(dump_bank(original))
        assert parsed.questions == original.questions
        assert parsed.cursor == 1
        assert parsed.state == "pending"
        assert parsed.next_question_text == "q2"

    def test_corrupt_cursor_falls_back_to_exhausted(self):
        # 越界游标会让同一道题被反复问，宁可当作已出完
        cases = {
            "负数": '{"v": 2, "cursor": -1, "questions": [{"question": "q1"}]}',
            "越界": '{"v": 2, "cursor": 5, "questions": [{"question": "q1"}]}',
            "字符串": '{"v": 2, "cursor": "2", "questions": [{"question": "q1"}]}',
            "布尔": '{"v": 2, "cursor": true, "questions": [{"question": "q1"}]}',
            "缺失": '{"v": 2, "questions": [{"question": "q1"}]}',
        }
        for name, raw in cases.items():
            assert parse_bank(raw).cursor == 1, name

    def test_garbage_and_unknown_state(self):
        assert parse_bank("not json").questions == []
        assert parse_bank('"a string"').questions == []
        bank = parse_bank('{"state": "weird", "questions": [{"question": "q"}]}')
        assert bank.state == "ready"
        # 题库里的非 dict 元素要丢掉，否则下游 q.get() 会炸
        assert parse_bank('{"questions": [1, null, {"question": "q"}]}').questions == [
            {"question": "q"}
        ]


class TestNeedsFollowUp:
    def test_low_score_escalates(self):
        assert needs_follow_up(FOLLOW_UP_SCORE_THRESHOLD - 1, LONG_ANSWER)[0] is True

    def test_boundary_score_does_not_escalate(self):
        assert needs_follow_up(FOLLOW_UP_SCORE_THRESHOLD, LONG_ANSWER)[0] is False

    def test_unscored_escalates(self):
        assert needs_follow_up(None, LONG_ANSWER)[0] is True

    def test_too_short_answer_escalates_even_with_good_score(self):
        short = "嗯" * (SHORT_ANSWER_CHARS - 1)
        escalate, reason = needs_follow_up(90, short)
        assert escalate is True and "过短" in reason


class TestPlanNextQuestion:
    def test_bank_hit_returns_index(self):
        plan = plan_next_question(_bank("q1", "q2", cursor=1), score=85, answer=LONG_ANSWER)
        assert plan.question == "q2"
        assert plan.source == "bank"
        assert plan.bank_index == 1

    def test_low_score_follows_up_without_consuming_bank(self):
        plan = plan_next_question(
            _bank("q1", "q2", cursor=1),
            score=40,
            answer=LONG_ANSWER,
            follow_up_factory=lambda: "你刚说的事务拆分，回滚时怎么保证一致？",
        )
        assert plan.source == "follow_up"
        assert plan.bank_index is None, "追问是绕道，不能吃掉题库里的题"
        assert "低于" in plan.reason

    def test_follow_up_failure_falls_back_to_bank(self):
        def boom() -> str:
            raise ValueError("模型没有给出追问")

        plan = plan_next_question(
            _bank("q1", "q2", cursor=1), score=40, answer=LONG_ANSWER, follow_up_factory=boom
        )
        assert plan.source == "bank"
        assert plan.question == "q2"
        assert any("追问生成失败" in w for w in plan.warnings)

    def test_exhausted_bank_generates_follow_up(self):
        plan = plan_next_question(
            _bank("q1", cursor=1),
            score=88,
            answer=LONG_ANSWER,
            follow_up_factory=lambda: "再深入讲讲？",
        )
        assert plan.source == "follow_up"
        assert plan.question == "再深入讲讲？"
        assert plan.bank_index is None

    def test_pending_bank_with_no_question_falls_back_to_follow_up(self):
        """后台还没补完题（only 1 题、cursor 已到 1）时不能问空题。"""
        plan = plan_next_question(
            _bank("q1", cursor=1, state="pending"),
            score=90,
            answer=LONG_ANSWER,
            follow_up_factory=lambda: "下一题？",
        )
        assert plan.source == "follow_up"

    def test_legacy_follow_up_used_when_everything_else_fails(self):
        plan = plan_next_question(
            _bank(cursor=0),
            score=90,
            answer=LONG_ANSWER,
            eval_follow_up="模型自带的下一题",
        )
        assert plan.source == "legacy"
        assert plan.question == "模型自带的下一题"

    def test_no_question_anywhere_returns_empty(self):
        plan = plan_next_question(_bank(cursor=0), score=90, answer=LONG_ANSWER)
        assert plan.question == ""
        assert plan.source == "none"
