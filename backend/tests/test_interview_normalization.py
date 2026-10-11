"""T14：面试评估结果规范化测试（B17/B18）。

B17：模型缺 score 时置 None 并告警，不伪造 0 分落库。
B18：continue 却没有下一题时转 summary，避免前端白题卡死。
"""

from __future__ import annotations

from app.services.interview_simulation_service import normalize_interview_evaluation


class TestScoreNormalization:
    def test_valid_score_passthrough(self):
        result, warnings = normalize_interview_evaluation(
            {"evaluation": "回答到位", "score": 85, "next_action": "continue",
             "follow_up": "追问一下并发模型", "overall_summary": None}
        )
        assert result["score"] == 85.0
        assert result["next_action"] == "continue"
        assert result["follow_up"] == "追问一下并发模型"
        assert warnings == []

    def test_missing_score_becomes_none_with_warning(self):
        result, warnings = normalize_interview_evaluation(
            {"evaluation": "评价", "next_action": "continue", "follow_up": "下一题"}
        )
        assert result["score"] is None
        assert any("未返回有效评分" in w for w in warnings)

    def test_string_score_is_corrected(self):
        result, warnings = normalize_interview_evaluation(
            {"evaluation": "评价", "score": " 72 ", "next_action": "continue", "follow_up": "x"}
        )
        assert result["score"] == 72.0
        assert any("评分格式异常" in w for w in warnings)

    def test_garbage_score_becomes_none(self):
        result, warnings = normalize_interview_evaluation(
            {"evaluation": "评价", "score": " excellent ", "next_action": "continue", "follow_up": "x"}
        )
        assert result["score"] is None
        assert any("未返回有效评分" in w for w in warnings)

    def test_score_clamped_to_0_100(self):
        result, _ = normalize_interview_evaluation(
            {"evaluation": "评价", "score": 150, "next_action": "continue", "follow_up": "x"}
        )
        assert result["score"] == 100.0


class TestActionNormalization:
    def test_continue_with_follow_up_kept(self):
        result, warnings = normalize_interview_evaluation(
            {"evaluation": "评价", "score": 80, "next_action": "continue", "follow_up": "追问"}
        )
        assert result["next_action"] == "continue"
        assert warnings == []

    def test_continue_without_follow_up_converts_to_summary(self):
        """B18 核心：空下一题不再返回 continue，前端不会白题卡死。"""
        result, warnings = normalize_interview_evaluation(
            {"evaluation": "评价", "score": 80, "next_action": "continue", "follow_up": ""}
        )
        assert result["next_action"] == "summary"
        assert result["follow_up"] is None
        assert any("未给出下一题" in w for w in warnings)

    def test_unknown_action_falls_back_to_continue(self):
        result, warnings = normalize_interview_evaluation(
            {"evaluation": "评价", "score": 80, "next_action": "finish", "follow_up": "追问"}
        )
        assert result["next_action"] == "continue"
        assert any("next_action" in w for w in warnings)

    def test_unknown_action_without_follow_up_becomes_summary(self):
        result, warnings = normalize_interview_evaluation(
            {"evaluation": "评价", "score": 80, "next_action": "finish", "follow_up": None}
        )
        assert result["next_action"] == "summary"
        assert any("next_action" in w for w in warnings)
        assert any("未给出下一题" in w for w in warnings)

    def test_summary_fields_preserved(self):
        result, warnings = normalize_interview_evaluation(
            {"evaluation": "最后评价", "score": 90, "next_action": "summary",
             "follow_up": None, "overall_summary": "整体表现不错"}
        )
        assert result["next_action"] == "summary"
        assert result["overall_summary"] == "整体表现不错"
        assert warnings == []

    def test_missing_fields_get_defaults(self):
        result, warnings = normalize_interview_evaluation({})
        assert result["evaluation"] == ""
        assert result["score"] is None
        assert result["next_action"] == "summary"  # 无 follow_up，回退后转 summary
        assert result["follow_up"] is None
        assert result["overall_summary"] is None
        assert len(warnings) >= 2
