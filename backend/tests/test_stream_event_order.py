"""SSE 事件顺序回归测试。

背景（见 docs/2026-10-11-慢与静默失败-体验审计清单.md 的 A4 / B13）：

- 旧顺序把 verification 排在 done 之前，而上下文校验要再跑一次 LLM，
  用户会经历"打字机结束 → 静默 3~8 秒 → done 把内容整体替换"的体验。
  现在 done 先发，verification（含引用标注）随后补发。
- 旧代码流式生成异常只写日志，前端收不到 error 事件，界面静默显示残缺回答。
"""

from __future__ import annotations

from unittest.mock import MagicMock

from langchain_core.messages import AIMessageChunk

from app.services.conversation_chat_service import ConversationChatService

CONTEXT = "彭禹弘，本科学历，河北传媒学院。"
SOURCE_DOCS = [{"source_name": "简历.txt", "content": CONTEXT, "score": 1.0}]


def _build_service() -> ConversationChatService:
    svc = ConversationChatService(
        conversation_store_service=MagicMock(),
        document_index_service=MagicMock(),
    )

    # 前置流程：给出非空上下文，才会触发上下文校验
    svc._prepare_chat = lambda *args, **kwargs: (
        "agent",           # normalized_mode
        "他的学历是什么",   # effective_query
        [],                # normalized_image_urls
        [],                # compressed_history
        CONTEXT,           # context
        SOURCE_DOCS,       # source_docs
        "他的学历是什么",   # user_content
    )

    svc.agent_llm = MagicMock()
    svc.agent_llm.stream.return_value = iter([AIMessageChunk(content="他是本科学历。")])

    # _prepare_chat 被替换后不会走初始化流程，这里补一个可用的 prompt
    svc._ensure_initialized = lambda: None
    svc.agent_prompt = MagicMock()
    svc.agent_prompt.invoke.return_value.to_messages.return_value = []

    svc.context_verification_service = MagicMock()
    svc.context_verification_service.generate_verification_report.return_value = {
        "verification": {
            "hallucination_risk": "low",
            "supported_claims": ["本科学历"],
            "unsupported_claims": [],
        },
        "cited_answer": "他是本科学历。[1]",
    }
    return svc


def test_done_comes_before_verification():
    """done 必须先到，验证结果（含引用标注）随后补发。"""
    events = list(_build_service().chat_stream("conv_1", "他的学历是什么", "agent", []))

    names = [e["event"] for e in events]
    assert "done" in names and "verification" in names
    assert names.index("done") < names.index("verification"), (
        f"done 必须先于 verification，实际顺序：{names}"
    )


def test_verification_carries_cited_answer():
    """引用标注依赖完整回答，只能随 verification 补发。"""
    events = list(_build_service().chat_stream("conv_1", "他的学历是什么", "agent", []))
    verification = next(e for e in events if e["event"] == "verification")
    done = next(e for e in events if e["event"] == "done")

    assert verification["data"]["cited_answer"] == "他是本科学历。[1]"
    # done 阶段只能给出未加标注的原始回答
    assert done["data"]["answer"] == "他是本科学历。"


def test_stream_error_emits_error_event():
    """流式中断要发 error，否则前端完全无感知。"""
    svc = _build_service()
    svc.agent_llm.stream.side_effect = RuntimeError("连接中断")

    events = list(svc.chat_stream("conv_1", "他的学历是什么", "agent", []))
    names = [e["event"] for e in events]

    assert "error" in names, f"流式异常应产出 error 事件，实际：{names}"


def test_verification_failure_does_not_break_done():
    """校验失败时，done 已经发出，不能反过来影响已完成的回答。"""
    svc = _build_service()
    svc.context_verification_service.generate_verification_report.side_effect = RuntimeError("校验挂了")

    events = list(svc.chat_stream("conv_1", "他的学历是什么", "agent", []))
    names = [e["event"] for e in events]

    assert "done" in names
    assert "verification" not in names, "校验失败不应产出 verification 事件"
