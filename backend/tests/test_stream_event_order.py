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
    # 模型改为按请求取客户端后，测试桩接到 _get_agent_llm/_get_qa_llm 接缝上
    svc._get_agent_llm = lambda: svc.agent_llm
    svc._get_qa_llm = lambda: svc.agent_llm

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


# ---------------------------------------------------------------------------
# T12：Agent 工具链进度事件与轮次耗尽降级（A5 / B11 / B12）
# ---------------------------------------------------------------------------

from langchain_core.messages import AIMessage  # noqa: E402

_TOOL_CALL = [{"name": "web_search", "args": {"q": "x"}, "id": "c1"}]


def _build_tool_service() -> ConversationChatService:
    svc = _build_service()
    # 首轮流式命中工具调用（bind_tools 状态下模型请求工具时通常无文本输出）
    svc.agent_llm.stream.return_value = iter([AIMessageChunk(content="", tool_calls=list(_TOOL_CALL))])
    svc.tool_dic = {"web_search": MagicMock(invoke=MagicMock(return_value="搜索结果内容"))}
    return svc


def test_tool_rounds_emit_status_events():
    """每个工具执行前要产出 tool_status 事件，正常完成不带降级标记。"""
    svc = _build_tool_service()
    svc.agent_llm.invoke.side_effect = [
        AIMessage(content="", tool_calls=list(_TOOL_CALL)),
        AIMessage(content="根据搜索结果，答案是……"),
    ]

    events = list(svc.chat_stream("conv_1", "搜一下", "agent", []))
    statuses = [e for e in events if e["event"] == "tool_status"]

    assert statuses, "工具执行前必须产出 tool_status 事件"
    assert statuses[0]["data"]["name"] == "web_search"
    assert statuses[0]["data"]["step"] == 1
    done = next(e for e in events if e["event"] == "done")
    assert "degraded" not in done["data"], "正常完成的回答不应带降级标记"


def test_round_exhaustion_marks_degraded():
    """工具轮耗尽返回的道歉/中间内容必须带 degraded + warnings，不能再伪装成正常回答。"""
    svc = _build_tool_service()
    # 每次都要求调用工具 → 6 轮耗尽，永远拿不到最终回答
    svc.agent_llm.invoke.return_value = AIMessage(content="", tool_calls=list(_TOOL_CALL))

    events = list(svc.chat_stream("conv_1", "搜一下", "agent", []))
    done = next(e for e in events if e["event"] == "done")

    assert done["data"].get("degraded") is True
    assert done["data"].get("warnings"), "轮次耗尽必须携带 warnings"


def test_tool_failure_marks_degraded():
    """单个工具执行失败也要让前端知道，而不是拿到看似正常的回答。"""
    svc = _build_tool_service()
    svc.tool_dic["web_search"].invoke.side_effect = RuntimeError("Key 无效")
    svc.agent_llm.invoke.side_effect = [
        AIMessage(content="", tool_calls=list(_TOOL_CALL)),
        AIMessage(content="根据搜索结果，答案是……"),
    ]

    events = list(svc.chat_stream("conv_1", "搜一下", "agent", []))
    done = next(e for e in events if e["event"] == "done")

    assert done["data"].get("degraded") is True
    assert any("失败" in w for w in done["data"]["warnings"])


def test_nonstream_agent_promotes_warnings():
    """非流式 agent 路径同样要把工具链告警带出 service 层。"""
    svc = _build_tool_service()
    svc.agent_llm.invoke.return_value = AIMessage(content="", tool_calls=list(_TOOL_CALL))

    result = svc.chat("conv_1", "搜一下", "agent", [])

    assert result["warnings"], "chat() 返回必须携带工具链 warnings"


# ---------------------------------------------------------------------------
# 轮次用尽后的收尾调用：不能再交白卷，也不能继续调工具
# ---------------------------------------------------------------------------


def _finalize_service():
    """工具轮必然耗尽 + 收尾调用返回一段正常回答。"""
    svc = _build_tool_service()
    svc.agent_llm.invoke.return_value = AIMessage(content="", tool_calls=list(_TOOL_CALL))
    qa = MagicMock()
    qa.invoke.return_value = AIMessage(content="汇总后的回答")
    svc._get_qa_llm = lambda: qa
    return svc, qa


def test_round_exhaustion_finalizes_from_tool_results():
    """轮次耗尽时用不带工具的收尾调用作答，而不是让用户看到"抱歉，我无法生成回答"。"""
    svc, qa = _finalize_service()

    events = list(svc.chat_stream("conv_1", "搜一下", "agent", []))
    done = next(e for e in events if e["event"] == "done")
    deltas = [e["data"]["text"] for e in events if e["event"] == "delta"]

    assert done["data"]["answer"] == "汇总后的回答"
    assert "汇总后的回答" in deltas, "收尾回答要按 delta 发给前端，否则界面不显示"
    # 仍然要告诉用户这不是模型本来的最终回答
    assert any("汇总生成" in w for w in done["data"]["warnings"])
    # 收尾调用必须带上工具结果与用户问题，且走不带工具的客户端
    # （_prepare_chat 被桩成固定 query="他的学历是什么"）
    prompt = qa.invoke.call_args[0][0]
    assert "用户问题：他的学历是什么" in prompt
    assert "搜索结果内容" in prompt


def test_nonstream_round_exhaustion_finalizes():
    """非流式路径与流式一致，轮次耗尽后也要收尾作答。"""
    svc, _qa = _finalize_service()

    result = svc.chat("conv_1", "搜一下", "agent", [])

    assert result["original_answer"] == "汇总后的回答"
    assert any("汇总生成" in w for w in result["warnings"])


def test_file_turn_skips_verification():
    """产出文件的那一轮跳过上下文校验。

    回答只有"已生成 + 下载链接"，没有可被文档反驳的事实断言，引用标注也无从标起。
    实测这一跳要再跑一次 LLM（5~33 秒），既烧额度又让 SSE 连接多挂几十秒，
    而 done 早就发完了，用户只是在等一个永远不会改变正文的回包。
    """
    svc = _build_tool_service()

    def fake_rounds(messages, tool_result, effective_query):
        files = [{"name": "resume.html", "url": "/api/v1/conversation/file/resume.html"}]
        tool_result["answer"] = "已生成 HTML 简历，下载链接见上方。"
        tool_result["files"] = files
        yield {"event": "files", "data": {"files": files}}

    svc._run_tool_rounds = fake_rounds

    events = list(svc.chat_stream("conv_1", "帮我生成简历", "agent", []))
    names = [e["event"] for e in events]

    assert "done" in names
    assert "verification" not in names, f"有文件产物时不该再跑校验，实际：{names}"
    svc.context_verification_service.generate_verification_report.assert_not_called()
