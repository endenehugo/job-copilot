"""降级可见化测试：验证失败必须说"未验证"，评分缺失必须说"未评分"。

背景（B1）：旧实现里 LLM 验证抛异常后返回两个空 claims，_assess_risk 算出
total=0 → ratio=0 → risk="low"，前端据此渲染**绿色**「幻觉风险：低」徽章。
验证根本没跑成功，界面却主动告诉用户"这回答可信"——反向误导，比不提示更糟。

（B3/B4/B5）：评分字段缺失旧实现一律填 0，0 是真实分数，会把"模型这次没返回"
伪装成"你的简历 0 分"，还会落库污染历史评分。
"""

from app.core.response import ok
from app.services.context_verification_service import ContextVerificationService as S
from app.services.resume_scoring_service import ResumeScoringService as R


# ---- 响应信封 ----

def test_ok_without_degradation_has_no_extra_fields():
    body = ok({"a": 1}, "done")
    assert body == {"code": "success", "message": "done", "data": {"a": 1}}
    assert "degraded" not in body and "warnings" not in body


def test_ok_with_warnings_marks_degraded():
    body = ok({"a": 1}, "done", warnings=["模型未返回总分", ""])
    assert body["degraded"] is True
    # 空字符串要被过滤掉，别给用户看一条空白提示
    assert body["warnings"] == ["模型未返回总分"]


def test_ok_with_degraded_flag_only():
    body = ok({}, "done", degraded=True)
    assert body["degraded"] is True
    assert body["warnings"] == []


# ---- 幻觉风险 ----

def test_verification_failure_is_unknown_not_low():
    """验证挂了不能报 low：low 会被前端渲染成绿色徽章。"""
    svc = S()

    class Boom:
        def invoke(self, _prompt):
            raise RuntimeError("dashscope 超时")

    svc._verifier_llm = Boom()
    result = svc.verify_answer("候选人有三年经验", "上下文文本")
    assert result["hallucination_risk"] == "unknown"
    assert result["verification_error"]


def test_no_context_is_unknown_not_low():
    result = S().verify_answer("", "")
    assert result["hallucination_risk"] == "unknown"


def test_successful_verification_still_assesses_risk():
    """正常路径不能被上面的改动误伤：有 claims 时仍按占比分级。"""
    svc = S()

    class FakeLLM:
        def invoke(self, _prompt):
            class R:
                content = '{"supported_claims": ["a"], "unsupported_claims": ["b", "c", "d"]}'
            return R()

    svc._verifier_llm = FakeLLM()
    # 3/4 无支撑 → 75% → high
    assert svc.verify_answer("回答", "上下文")["hallucination_risk"] == "high"


# ---- 评分缺失 ----

def test_missing_scores_are_none_with_warnings():
    result = R._validate_and_normalize({"strengths": ["有 RAG 经验"]})
    assert result["total_score"] is None
    assert result["dimensions"]["skill_match"] is None
    assert any("总分" in w for w in result["warnings"])


def test_zero_score_is_kept_as_zero():
    """回归保护：0 分是合法分数，不能被当成缺失。"""
    result = R._validate_and_normalize(
        {
            "total_score": 0,
            "dimensions": {
                "skill_match": 0,
                "project_relevance": 0,
                "expression_quality": 0,
                "job_fitness": 0,
            },
        }
    )
    assert result["total_score"] == 0
    assert result["dimensions"]["skill_match"] == 0
    assert result["warnings"] == []


def test_unparsable_score_is_none_not_zero():
    result = R._validate_and_normalize({"total_score": "优秀"})
    assert result["total_score"] is None
    assert result["warnings"]
