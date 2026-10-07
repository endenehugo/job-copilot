"""知识库动态更新（AI 审核）的回归测试。

不依赖真实 LLM 与数据库：审核服务的输出归一化逻辑用桩 LLM 验证；
入库/索引链路由真实 LLM 冒烟覆盖。
"""

import pytest

from app.services.knowledge_review_service import KNOWLEDGE_CATEGORIES, KnowledgeReviewService


def _service_with_fake_llm(response: str) -> KnowledgeReviewService:
    service = KnowledgeReviewService()
    service._invoke_llm = lambda system, user: response
    return service


def test_review_normalizes_llm_output():
    service = _service_with_fake_llm(
        '{"related": true, "duplicate": false, "category": "mysql索引", '
        '"title": "  MySQL 索引原理  ", "reason": "属于数据库面试八股"}'
    )
    result = service.review("MySQL 为什么用 B+ 树做索引", ["已有条目一"])

    assert result["related"] is True
    assert result["duplicate"] is False
    # 非法分类回退到 general
    assert result["category"] == "general"
    assert result["title"] == "MySQL 索引原理"


def test_review_requires_related_flag_and_handles_garbage():
    service = _service_with_fake_llm("这不是 JSON")
    with pytest.raises(ValueError):
        service.review("今天天气不错", [])


def test_generate_candidates_parses_and_filters_empty():
    service = _service_with_fake_llm(
        '{"entries": ['
        '{"title": "Redis 持久化", "category": "database", "content": "RDB 与 AOF 的区别与取舍……"},'
        '{"title": "", "content": ""}'
        ']}'
    )
    candidates = service.generate_candidates(["已有条目"], 2)

    assert len(candidates) == 1
    assert candidates[0]["title"] == "Redis 持久化"
    assert candidates[0]["category"] == "database"


def test_categories_enum_contains_builtin_five():
    for cat in ("python_backend", "database", "linux", "flask_engineering", "agent_rag"):
        assert cat in KNOWLEDGE_CATEGORIES
