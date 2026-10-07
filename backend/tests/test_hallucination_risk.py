"""幻觉风险分级规则测试（纯逻辑，不依赖 LLM）。

背景：全链路验证发现模型编造的量化数据（"召回率提升 22.6%"等 5 条）
占比仅 ~10%，旧规则判 low——编造具体数字比含糊其辞更危险，必须升级。
"""

from app.services.context_verification_service import ContextVerificationService as S


def test_ratio_thresholds_unchanged_without_numbers():
    # 占比 50% → high
    assert S._assess_risk(["a"] * 5, ["x"] * 5) == "high"
    # 占比 25% → medium
    assert S._assess_risk(["a"] * 6, ["x"] * 2) == "medium"
    # 占比 10%、无量化数据 → low（维持原行为）
    assert S._assess_risk(["a"] * 9, ["涉及某内部流程"]) == "low"


def test_single_numeric_unsupported_escalates_to_medium():
    # 占比 5%（<10%），但无支撑断言含编造百分比 → 至少 medium
    risk = S._assess_risk(["a"] * 19, ["混合检索召回率较单 FAISS 提升 22.6%"])
    assert risk == "medium"


def test_numeric_with_ratio_over_ten_percent_is_high():
    # 占比 10% 且含编造百分比 → high
    risk = S._assess_risk(["a"] * 9, ["混合检索召回率较单 FAISS 提升 22.6%"])
    assert risk == "high"


def test_two_numeric_unsupported_escalate_to_high():
    # 全链路验证中的真实场景：多条编造量化数据 → high
    risk = S._assess_risk(
        ["a"] * 46,
        [
            "混合检索平均召回率较单 FAISS 提升 22.6%（内部测试集）",
            "评分结果与人工初筛一致性达 89%（抽样 50 份 JD+简历对）",
            "技术笔记获 400+ 收藏",
            "3 位同学获面试邀约",
        ],
    )
    assert risk == "high"


def test_small_ratio_with_two_numeric_still_high():
    # 占比仅 ~9%（两条量化 / 23 条）也判 high：编造数字不看出占比下限
    risk = S._assess_risk(["a"] * 21, ["一致性达 89%", "耗时约 3 倍"])
    assert risk == "high"


def test_plain_single_digit_claims_do_not_trigger():
    # 非量化断言（仅单个数字）不触发升级
    risk = S._assess_risk(["a"] * 9, ["涉及某个内部流程"])
    assert risk == "low"
