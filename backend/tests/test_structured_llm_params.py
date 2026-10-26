"""问答关键路径上的结构化调用必须关思考。

2026-10-26 线上分段计时（RAG 3 次请求）：校验占 79%（11s），其 LLM 输入只有 226 token、
输出却 1049~1414 token——思考过程被算进了输出。关掉后 1.6s / 113 token，判定结果一致。
重排同理：输出只是一个编号数组。
"""

from __future__ import annotations

import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def _clear_cache():
    from app.core.llm_factory import build_chat_llm

    build_chat_llm.cache_clear()
    yield
    build_chat_llm.cache_clear()


class _StubLLM:
    """构造参数被捕获即可；rerank 会真的调 invoke，给个能解析的返回值。"""

    def invoke(self, prompt):
        class _Result:
            content = "[0, 1, 2, 3]"

        return _Result()


def _capture(monkeypatch) -> dict:
    captured: dict = {}

    def fake_build_chat_llm(**kwargs):
        captured.update(kwargs)
        return _StubLLM()

    monkeypatch.setattr("app.core.llm_factory.build_chat_llm", fake_build_chat_llm)
    return captured


def test_verifier_disables_thinking(monkeypatch) -> None:
    from app.services.context_verification_service import ContextVerificationService

    captured = _capture(monkeypatch)
    ContextVerificationService()._get_llm()
    assert captured["enable_thinking"] is False
    assert captured["max_tokens"] == settings.context_verify_max_tokens
    assert captured["model"] == settings.llm_small_model


def test_rerank_listwise_disables_thinking(monkeypatch) -> None:
    from app.services.document_index_service import DocumentIndexService

    captured = _capture(monkeypatch)
    service = DocumentIndexService()
    service._rerank_with_listwise("问题", _docs(6), top_k=4)
    assert captured["enable_thinking"] is False
    assert captured["max_tokens"] == 120


def _docs(count: int):
    from langchain_core.documents import Document

    return [Document(page_content=f"文档{i}", metadata={"id": i}) for i in range(count)]
