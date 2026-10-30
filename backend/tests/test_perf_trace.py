"""请求级分段计时（perf_trace）单元测试。"""

from __future__ import annotations

import contextvars
import json
import logging
import threading
import time
from unittest.mock import patch

from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from app.core import perf_trace as pt
from app.core.llm_factory import FallbackChatModel

QUOTA_MSG = (
    "request_id: req-test \n status_code: 400 \n "
    "code: AllocationQuota.FreeTierOnly \n message: 免费额度用完即停"
)


def test_span_without_trace_is_noop():
    with pt.span("db.save") as s:
        assert s is None
    assert pt.current_trace() is None


def test_nested_spans_record_parent_and_order():
    with pt.trace_scope("chat", mode="memory") as t:
        with pt.span("prepare"):
            with pt.span("db.history"):
                pass
        with pt.span("answer"):
            pass
    assert [s["name"] for s in t.spans] == ["prepare", "db.history", "answer"]
    parents = {s["name"]: s["parent"] for s in t.spans}
    assert parents == {"prepare": None, "db.history": "prepare", "answer": None}
    assert [s["seq"] for s in t.spans] == [1, 2, 3]


def test_span_yields_dict_whose_attrs_can_be_written():
    with pt.trace_scope("chat") as t:
        with pt.span("compress") as s:
            s["attrs"]["triggered"] = True
    assert t.spans[0]["attrs"]["triggered"] is True


def test_stages_group_by_prefix_and_exclude_llm():
    with pt.trace_scope("chat") as t:
        with pt.span("db.history"):
            time.sleep(0.01)
        with pt.span("db.save"):
            time.sleep(0.01)
        with pt.span("tool.resume_generate_tool"):
            time.sleep(0.01)
        pt.record_llm_call("m", 5.0, in_tokens=10, out_tokens=3)
    stages = t.stages()
    assert stages["db"] >= 20 and stages["tool"] >= 10
    assert "llm" not in stages and "llm.call" not in stages
    assert t.llm_summary() == (1, 5.0)


def test_nested_span_time_is_not_double_counted_in_stages():
    """父子 span 都在计时：阶段汇总只累加每个顶层前缀下"最外层"的 span，避免重复计算。"""
    with pt.trace_scope("chat") as t:
        with pt.span("retrieve"):
            time.sleep(0.02)
            with pt.span("retrieve.builtin"):
                time.sleep(0.02)
    outer = next(s for s in t.spans if s["name"] == "retrieve")
    assert abs(t.stages()["retrieve"] - outer["dur_ms"]) < 1.0


def test_record_llm_call_parent_is_current_span():
    with pt.trace_scope("chat") as t:
        with pt.span("compress"):
            pt.record_llm_call("small", 3.0)
    llm = next(s for s in t.spans if s["name"] == "llm.call")
    assert llm["parent"] == "compress"
    assert llm["attrs"]["model"] == "small"
    assert llm["dur_ms"] == 3.0


def test_record_span_uses_current_parent():
    with pt.trace_scope("chat") as t:
        with pt.span("answer"):
            pt.record_span("tool.x", 4.0, round=2)
    tool = next(s for s in t.spans if s["name"] == "tool.x")
    assert tool["parent"] == "answer" and tool["dur_ms"] == 4.0 and tool["attrs"]["round"] == 2


def test_exception_inside_span_propagates_and_marks_error():
    with pt.trace_scope("chat") as t:
        try:
            with pt.span("answer"):
                raise ValueError("x")
        except ValueError:
            pass
    assert t.spans[0]["attrs"]["error"] == "ValueError"


def test_log_line_is_json_without_text_fields(caplog):
    with caplog.at_level(logging.INFO, logger="app.perf"):
        with pt.trace_scope("chat", mode="rag", conversation_id="c1"):
            with pt.span("answer"):
                pass
    line = next(r.getMessage() for r in caplog.records if r.name == "app.perf")
    assert line.startswith("PERF ")
    data = json.loads(line[5:])
    assert data["v"] == 1 and data["endpoint"] == "chat"
    assert data["mode"] == "rag" and data["conversation_id"] == "c1"
    assert {"trace_id", "total_ms", "stages", "llm_calls", "llm_ms", "spans"} <= set(data)


def test_set_attr_is_included_in_log_dict():
    with pt.trace_scope("chat") as t:
        pt.set_attr("model", "qwen3.8-flash")
    assert t.to_log_dict()["model"] == "qwen3.8-flash"


def test_same_stage_child_does_not_duplicate_metric():
    """retrieve.conversation 属于 retrieve 同一个阶段：不能作为子阶段单独出现在响应头里，
    否则同名指标出现两次，压测侧解析时后者会覆盖前者的真实值。"""
    with pt.trace_scope("chat") as t:
        with pt.span("retrieve"):
            with pt.span("retrieve.conversation"):
                time.sleep(0.01)
    assert "retrieve" in t.stages() and "retrieve" not in t.substages()
    assert t.to_server_timing().count("retrieve;") == 1


def test_substage_stays_out_of_top_level_stages():
    """rerank 是 retrieve 的子 span 但阶段名不同：它只进 substages，否则阶段相加会超过 total。"""
    with pt.trace_scope("chat") as t:
        with pt.span("retrieve"):
            with pt.span("rerank"):
                time.sleep(0.02)
            time.sleep(0.01)
    stages, subs = t.stages(), t.substages()
    assert "rerank" not in stages and subs["rerank"] >= 20
    assert stages["retrieve"] >= 30
    assert sum(stages.values()) <= t.total_ms() + 1.0
    assert t.to_log_dict()["substages"]["rerank"] >= 20


def test_sum_stages_never_exceeds_total():
    with pt.trace_scope("chat") as t:
        for _ in range(3):
            with pt.span("db.history"):
                time.sleep(0.005)
    data = t.to_log_dict()
    assert data["sum_stages_ms"] <= data["total_ms"] + 1.0


def test_server_timing_marks_substages():
    with pt.trace_scope("chat") as t:
        with pt.span("retrieve"):
            with pt.span("rerank"):
                time.sleep(0.01)
    assert 'rerank;dur=' in t.to_server_timing() and 'desc="of:sub"' in t.to_server_timing()


def test_server_timing_format():
    with pt.trace_scope("chat") as t:
        with pt.span("db.save"):
            pass
        pt.record_llm_call("m", 7.0)
    parts = [p.strip() for p in t.to_server_timing().split(",")]
    assert parts[0].startswith("total;dur=")
    assert any(p.startswith("db;dur=") for p in parts)
    assert any(p.startswith('llm;dur=7.0;desc="n=1"') for p in parts)


def test_server_timing_stage_names_are_header_safe():
    with pt.trace_scope("chat") as t:
        with pt.span("tool.Weird Name/1"):
            pass
    for part in t.to_server_timing().split(","):
        name = part.strip().split(";", 1)[0]
        assert name and all(c.islower() or c.isdigit() or c == "_" for c in name), name


def test_context_visible_in_copied_thread_context():
    seen = {}
    with pt.trace_scope("chat"):
        ctx = contextvars.copy_context()

        def work():
            with pt.span("tool.x"):
                seen["ok"] = pt.current_trace() is not None

        thread = threading.Thread(target=lambda: ctx.run(work))
        thread.start()
        thread.join()
    assert seen["ok"]


def test_nested_trace_scope_reuses_outer():
    with pt.trace_scope("chat") as outer:
        with pt.trace_scope("inner") as inner:
            assert inner is outer


def test_trace_is_cleared_after_scope():
    with pt.trace_scope("chat"):
        pass
    assert pt.current_trace() is None


def test_helper_failures_never_raise(monkeypatch):
    with pt.trace_scope("chat"):
        monkeypatch.setattr(pt, "_now_ms", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        pt.record_llm_call("m", 1.0)
        pt.record_span("x", 1.0)


def test_configure_perf_logging_is_idempotent():
    pt.configure_perf_logging()
    pt.configure_perf_logging()
    handlers = [h for h in pt.PERF_LOGGER.handlers if getattr(h, "_perf_handler", False)]
    assert len(handlers) == 1
    assert pt.PERF_LOGGER.level == logging.INFO


# ---------------------------------------------------------------------------
# LLM 调用统一记录（QuotaFallbackMixin）
# ---------------------------------------------------------------------------

def _llm(**kwargs) -> FallbackChatModel:
    return FallbackChatModel(model="qwen-plus", temperature=0.1, max_retries=0, **kwargs)


def test_generate_records_llm_call_and_fallback():
    def fake_generate(self, messages, stop=None, run_manager=None, **kw):
        if self.model_name == "qwen-plus":
            raise ValueError(QUOTA_MSG)
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="ok"))],
            llm_output={"token_usage": {"input_tokens": 11, "output_tokens": 4}},
        )

    with patch("langchain_community.chat_models.tongyi.ChatTongyi._generate", fake_generate):
        llm = _llm(fallback_models=["qwen-flash"])
        with pt.trace_scope("chat") as t:
            with pt.span("answer"):
                llm.invoke("hi")
    calls = [s for s in t.spans if s["name"] == "llm.call"]
    assert [(c["attrs"]["model"], c["attrs"].get("error"), c["attrs"]["fallback"]) for c in calls] == [
        ("qwen-plus", "ValueError", False),
        ("qwen-flash", None, True),
    ]
    assert calls[1]["attrs"]["in_tokens"] == 11 and calls[1]["attrs"]["out_tokens"] == 4
    assert calls[1]["parent"] == "answer"


def test_generate_token_usage_accepts_openai_style_keys():
    def fake_generate(self, messages, stop=None, run_manager=None, **kw):
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="ok"))],
            llm_output={"token_usage": {"prompt_tokens": 7, "completion_tokens": 2}},
        )

    with patch("langchain_community.chat_models.tongyi.ChatTongyi._generate", fake_generate):
        with pt.trace_scope("chat") as t:
            _llm(fallback_models=[]).invoke("hi")
    call = next(s for s in t.spans if s["name"] == "llm.call")
    assert (call["attrs"]["in_tokens"], call["attrs"]["out_tokens"]) == (7, 2)


def test_generate_without_token_usage_leaves_tokens_out():
    def fake_generate(self, messages, stop=None, run_manager=None, **kw):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

    with patch("langchain_community.chat_models.tongyi.ChatTongyi._generate", fake_generate):
        with pt.trace_scope("chat") as t:
            _llm(fallback_models=[]).invoke("hi")
    call = next(s for s in t.spans if s["name"] == "llm.call")
    assert "in_tokens" not in call["attrs"] and "out_tokens" not in call["attrs"]


def test_generate_non_quota_error_is_recorded_and_still_raised():
    def fake_generate(self, messages, stop=None, run_manager=None, **kw):
        raise ValueError("Connection refused")

    with patch("langchain_community.chat_models.tongyi.ChatTongyi._generate", fake_generate):
        with pt.trace_scope("chat") as t:
            try:
                _llm(fallback_models=["qwen-flash"]).invoke("hi")
                raised = False
            except ValueError:
                raised = True
    assert raised
    calls = [s for s in t.spans if s["name"] == "llm.call"]
    assert len(calls) == 1 and calls[0]["attrs"]["error"] == "ValueError"


def test_generate_without_trace_behaves_as_before():
    def fake_generate(self, messages, stop=None, run_manager=None, **kw):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

    with patch("langchain_community.chat_models.tongyi.ChatTongyi._generate", fake_generate):
        llm = _llm(fallback_models=["qwen-flash"])
        assert llm.invoke("hi").content == "ok"
    assert llm.model_name == "qwen-plus" and pt.current_trace() is None


def test_stream_records_one_call_covering_the_whole_stream():
    def fake_stream(self, messages, stop=None, run_manager=None, **kw):
        for text in ("a", "b"):
            time.sleep(0.01)
            yield ChatGenerationChunk(message=AIMessageChunk(content=text))

    with patch("langchain_community.chat_models.tongyi.ChatTongyi._stream", fake_stream):
        with pt.trace_scope("chat") as t:
            chunks = list(_llm(fallback_models=[]).stream("hi"))
    assert "".join(c.content for c in chunks) == "ab"
    calls = [s for s in t.spans if s["name"] == "llm.call"]
    assert len(calls) == 1 and calls[0]["dur_ms"] >= 20


def test_stream_records_even_when_consumer_stops_early():
    def fake_stream(self, messages, stop=None, run_manager=None, **kw):
        for text in ("a", "b", "c"):
            yield ChatGenerationChunk(message=AIMessageChunk(content=text))

    with patch("langchain_community.chat_models.tongyi.ChatTongyi._stream", fake_stream):
        with pt.trace_scope("chat") as t:
            iterator = _llm(fallback_models=[])._stream([])
            next(iterator)
            iterator.close()
    assert len([s for s in t.spans if s["name"] == "llm.call"]) == 1


# ---------------------------------------------------------------------------
# chat 流程打点
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock  # noqa: E402

from app.services.conversation_chat_service import ConversationChatService  # noqa: E402


def _service_with_history(n_messages):
    svc = ConversationChatService(
        conversation_store_service=MagicMock(), document_index_service=MagicMock()
    )
    svc._ensure_initialized = lambda: None
    store = svc.conversation_store_service
    store.get_conversation_summary.return_value = ("", 0)
    store.get_messages_after.return_value = [
        {"id": i, "role": "user" if i % 2 == 0 else "assistant", "content": "x"}
        for i in range(n_messages)
    ]
    store.get_conversation_detail.return_value = {"conversation_id": "c1", "messages": []}
    svc.document_index_service.get_context_with_details.return_value = {"context": "", "documents": []}
    svc.context_compression_service = MagicMock()
    svc.context_compression_service.compress_history.return_value = ([], "", 0)
    # 后台压缩分支会调 with_summary 拼旧摘要，mock 掉也要保持"返回历史列表"的语义
    svc.context_compression_service.with_summary.side_effect = lambda history, summary: list(history)
    svc.qa_prompt = MagicMock()
    svc.qa_prompt.invoke.return_value.to_messages.return_value = []
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content="答案")
    svc._get_qa_llm = lambda: llm
    return svc


def _names(trace):
    return [s["name"] for s in trace.spans]


def test_memory_chat_emits_expected_spans():
    svc = _service_with_history(2)
    with pt.trace_scope("chat") as t:
        svc.chat("c1", "你好吗", "memory", [])
    names = _names(t)
    for expected in ("db.writable", "db.history", "compress", "answer", "db.save", "db.detail"):
        assert expected in names, names
    assert "retrieve" not in names
    compress = next(s for s in t.spans if s["name"] == "compress")
    assert compress["attrs"]["triggered"] is False and compress["attrs"]["messages"] == 2
    assert t.attrs["mode"] == "memory"


def test_compress_span_marks_triggered_when_watermark_moves():
    # 超过硬上限（40）才会同步压缩；阈值与上限之间是后台压缩，见下一条用例
    svc = _service_with_history(42)
    svc.context_compression_service.compress_history.return_value = ([], "新摘要", 9)
    with pt.trace_scope("chat") as t:
        svc.chat("c1", "你好吗", "memory", [])
    compress = next(s for s in t.spans if s["name"] == "compress")
    assert compress["attrs"]["triggered"] is True and compress["attrs"]["messages"] == 42
    assert compress["attrs"]["deferred"] is False


def test_compress_is_deferred_between_threshold_and_cap(monkeypatch):
    """攒到阈值时不再同步压：本轮直接用全量历史，只把任务排进后台。"""
    from app.services import context_compression_scheduler as scheduler

    scheduled: list[str] = []
    monkeypatch.setattr(scheduler, "schedule", lambda conversation_id: scheduled.append(conversation_id) or True)
    svc = _service_with_history(14)
    with pt.trace_scope("chat") as t:
        svc.chat("c1", "你好吗", "memory", [])
    compress = next(s for s in t.spans if s["name"] == "compress")
    assert compress["attrs"]["deferred"] is True and compress["attrs"]["triggered"] is False
    assert scheduled == ["c1"]
    # 没有同步的摘要调用（mock 的 LLM 不会产生 llm.call span，所以只看调用次数）
    assert svc.context_compression_service.compress_history.call_count == 0


def test_llm_call_inside_compress_has_compress_as_parent():
    svc = _service_with_history(42)

    def compress(history, old_summary="", max_messages=20, target_messages=None):
        pt.record_llm_call("small", 2.0)  # 压缩服务内部的摘要调用
        return ([], "新摘要", 9)

    svc.context_compression_service.compress_history.side_effect = compress
    with pt.trace_scope("chat") as t:
        svc.chat("c1", "你好吗", "memory", [])
    llm = next(s for s in t.spans if s["name"] == "llm.call")
    assert llm["parent"] == "compress"


def test_tool_span_named_after_tool():
    svc = _service_with_history(0)
    tool = MagicMock()
    tool.invoke.return_value = "ok"
    svc.tool_dic = {"multiply_tool": tool}
    with pt.trace_scope("chat") as t:
        svc._execute_tool_call({"name": "multiply_tool", "args": {"a": 1, "b": 2}})
    assert "tool.multiply_tool" in _names(t)


def test_failing_tool_still_returns_failure_and_records_span():
    svc = _service_with_history(0)
    tool = MagicMock()
    tool.invoke.side_effect = RuntimeError("坏了")
    svc.tool_dic = {"x_tool": tool}
    with pt.trace_scope("chat") as t:
        content, failed = svc._execute_tool_call({"name": "x_tool", "args": {}})
    assert failed is True and "坏了" in content
    assert "tool.x_tool" in _names(t)


def test_agent_chat_has_round_and_tool_spans():
    svc = _service_with_history(0)
    tool = MagicMock()
    tool.invoke.return_value = "6"
    svc.tool_dic = {"multiply_tool": tool}
    first = AIMessage(content="", tool_calls=[{"name": "multiply_tool", "args": {"a": 2, "b": 3}, "id": "t1"}])
    final = AIMessage(content="结果是 6")
    agent_llm = MagicMock()
    agent_llm.invoke.side_effect = [first, final]
    svc._get_agent_llm = lambda: agent_llm
    svc.agent_prompt = MagicMock()
    svc.agent_prompt.invoke.return_value.to_messages.return_value = []
    with pt.trace_scope("chat") as t:
        result = svc.chat("c1", "算一下", "agent", [])
    assert result["answer"] == "结果是 6"
    names = _names(t)
    assert names.count("agent.round") == 2 and "tool.multiply_tool" in names


def test_rag_chat_has_retrieve_and_verify_spans():
    svc = _service_with_history(2)
    svc.document_index_service.get_context_with_details.return_value = {
        "context": "上下文", "documents": [{"source_name": "a"}]}
    svc.context_verification_service = MagicMock()
    svc.context_verification_service.generate_verification_report.return_value = {
        "verification": {}, "cited_answer": "答案[1]"}
    with pt.trace_scope("chat") as t:
        svc.chat("c1", "问题", "rag", [])
    names = _names(t)
    assert "retrieve" in names and "verify" in names


def test_chat_without_trace_is_unchanged():
    svc = _service_with_history(2)
    result = svc.chat("c1", "你好吗", "memory", [])
    assert result["answer"] == "答案" and pt.current_trace() is None


def test_stream_memory_emits_spans_and_events_are_unchanged():
    svc = _service_with_history(2)
    with pt.trace_scope("chat_stream") as t:
        events = list(svc.chat_stream("c1", "你好吗", "memory", []))
    names = _names(t)
    for expected in ("db.writable", "compress", "answer", "db.save", "db.detail"):
        assert expected in names, names
    # memory 模式不检索，source_docs 恒为空；空 sources 事件没有意义，不再下发
    # （有文件产物时也不发，避免"参考来源"被误读成引用了用户上传的旧文档）
    assert [e["event"] for e in events] == ["meta", "delta", "done"]


def test_stream_agent_records_streamed_round_and_tool_rounds():
    svc = _service_with_history(0)
    tool = MagicMock()
    tool.invoke.return_value = "6"
    svc.tool_dic = {"multiply_tool": tool}
    streamed = AIMessageChunk(
        content="", tool_call_chunks=[], tool_calls=[{"name": "multiply_tool", "args": {"a": 2, "b": 3}, "id": "t1"}]
    )
    agent_llm = MagicMock()
    agent_llm.stream.return_value = iter([streamed])
    first_round = AIMessage(content="", tool_calls=[{"name": "multiply_tool", "args": {"a": 2, "b": 3}, "id": "t2"}])
    agent_llm.invoke.side_effect = [first_round, AIMessage(content="结果是 6")]
    svc._get_agent_llm = lambda: agent_llm
    svc.agent_prompt = MagicMock()
    svc.agent_prompt.invoke.return_value.to_messages.return_value = []
    with pt.trace_scope("chat_stream") as t:
        events = list(svc.chat_stream("c1", "算一下", "agent", []))
    names = _names(t)
    assert names.count("agent.round") == 3  # 首轮流式 + 工具轮里的两次 invoke
    assert "tool.multiply_tool" in names
    assert events[-1]["event"] == "done"


# ---------------------------------------------------------------------------
# 检索 / 重排打点
# ---------------------------------------------------------------------------

from langchain_core.documents import Document  # noqa: E402

from app.services.document_index_service import DocumentIndexService  # noqa: E402


def _docs(n):
    return [Document(page_content=f"内容{i}", metadata={"original_name": "a.txt"}) for i in range(n)]


def test_retrieval_spans_for_conversation_and_builtin():
    service = DocumentIndexService(builtin_knowledge_service=MagicMock())
    service.builtin_knowledge_service.retrieve.return_value = []
    service._load_conversation_db = lambda cid: object()
    service._hybrid_retrieve = lambda db, cid, q, limit: _docs(2)
    with pt.trace_scope("chat") as t:
        service.get_context_with_details("c1", "问题", limit=4)
    names = _names(t)
    assert "retrieve.conversation" in names and "retrieve.builtin" in names
    assert "retrieve.public" not in names


def test_public_retrieval_span_when_conversation_has_no_hits():
    service = DocumentIndexService()
    service._load_conversation_db = lambda cid: None
    retriever = MagicMock()
    retriever.get_relevant_documents.return_value = _docs(1)
    service._load_public_retriever = lambda: retriever
    with pt.trace_scope("chat") as t:
        service.get_context_with_details("c1", "问题", limit=4)
    assert "retrieve.public" in _names(t)


def test_rerank_span_wraps_the_whole_strategy():
    service = DocumentIndexService()
    service._rerank_with_bge = lambda q, d: None
    service._rerank_with_listwise = lambda q, d, k: d[:k]
    with pt.trace_scope("chat") as t:
        result = service._rerank_docs("问题", _docs(6), top_k=4)
    assert len(result) == 4
    rerank = next(s for s in t.spans if s["name"] == "rerank")
    assert rerank["attrs"]["strategy"] == "listwise" and rerank["attrs"]["docs"] == 6


def test_rerank_not_recorded_when_no_rerank_needed():
    service = DocumentIndexService()
    with pt.trace_scope("chat") as t:
        assert len(service._rerank_docs("问题", _docs(2), top_k=4)) == 2
    assert "rerank" not in _names(t)


def test_rerank_fallback_to_rrf_keeps_working_and_is_still_timed():
    service = DocumentIndexService()
    service._rerank_with_bge = lambda q, d: None

    def boom(q, d, k):
        raise RuntimeError("模型挂了")

    service._rerank_with_listwise = boom
    with pt.trace_scope("chat") as t:
        result = service._rerank_docs("问题", _docs(6), top_k=4, lists=[_docs(6)])
    assert len(result) == 4
    assert "rerank" in _names(t)
