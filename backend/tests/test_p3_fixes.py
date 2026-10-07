"""P3 旧债修复的回归测试。

覆盖五项修复 + 内置知识库三级检索接入：
① interview_sessions.total_score 列类型 Float（小数分不再被截断）
② 索引重建原子化（embedding 失败时旧索引保留）
③ Agent 工具调用异常转为错误 ToolMessage（消息序列完整）
④ BGE Reranker 实例懒加载缓存（不再每次检索重新加载模型）
⑤ MultiplyTool 参数校验（错误返回字符串而非抛异常）
⑥ 会话/公共索引都落空时兜底检索内置知识库
"""

import sys
import tempfile
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import UploadFile  # noqa: F401  仅确保 fastapi 可用
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy import Float

from app.repositories.interview_repository import InterviewSession
from app.services.conversation_chat_service import ConversationChatService
from app.services.document_index_service import DocumentIndexService
from app.tools import MultiplyTool


def test_interview_total_score_is_float_column():
    column = InterviewSession.__table__.c.total_score
    assert isinstance(column.type, Float), f"total_score 应为 Float，实际 {column.type}"


def test_rebuild_index_preserves_old_index_on_embedding_failure():
    import os
    from langchain_community.vectorstores import FAISS

    service = DocumentIndexService()

    with tempfile.TemporaryDirectory() as base:
        index_dir = os.path.join(base, "conv_test")
        parsed_dir = os.path.join(base, "parsed")
        os.makedirs(index_dir)
        os.makedirs(parsed_dir)
        parsed_path = os.path.join(parsed_dir, "doc_1.txt")
        with open(parsed_path, "w", encoding="utf-8") as f:
            f.write("新内容")
        # 先手工放一份旧索引
        FAISS.from_texts(
            ["旧索引内容一", "旧索引内容二", "旧索引内容三"],
            DeterministicEmbeddings(),
        ).save_local(index_dir)
        assert any(name.endswith(".faiss") for name in os.listdir(index_dir))

        documents = [{
            "document_id": "doc_1",
            "original_name": "a.txt",
            "parsed_text_path": parsed_path,
        }]
        service._get_conversation_index_dir = lambda cid: index_dir
        service._ensure_embeddings = lambda: "embeddings"

        with _patched("app.services.document_index_service.FAISS.from_texts", RuntimeError("API 挂了")):
            try:
                service.rebuild_conversation_index("conv_test", documents)
                raised = False
            except RuntimeError:
                raised = True

        assert raised, "embedding 失败应向上抛出"
        # 旧索引必须原样保留（修复前先删后建会丢）
        assert any(name.endswith(".faiss") for name in os.listdir(index_dir))
        # 临时目录应被清理
        assert not os.path.isdir(index_dir + ".tmp")


class DeterministicEmbeddings:
    """最小 Embedding 桩：直接满足 FAISS.from_texts 的接口。"""

    def embed_documents(self, texts):
        return [[float(len(t))] * 4 for t in texts]

    def embed_query(self, text):
        return [float(len(text))] * 4


class _patched:
    """让 FAISS.from_texts 抛指定异常的上下文管理器。"""

    def __init__(self, target, exc):
        import unittest.mock as mock
        self._patcher = mock.patch(target, side_effect=exc)

    def __enter__(self):
        return self._patcher.start()

    def __exit__(self, *exc_info):
        self._patcher.stop()


def _make_chat_service_with_failing_tool():
    service = ConversationChatService(
        conversation_store_service=MagicMock(),
        document_index_service=MagicMock(),
    )

    class BoomTool:
        name = "boom_tool"

        def invoke(self, args):
            raise RuntimeError("工具内部炸了")

    service.tool_dic = {"boom_tool": BoomTool()}
    return service


def test_agent_tool_exception_returns_tool_message():
    service = _make_chat_service_with_failing_tool()
    responses = [
        AIMessage(
            content="",
            tool_calls=[{"name": "boom_tool", "args": {}, "id": "call_1"}],
        ),
        AIMessage(content="done"),
    ]
    service.agent_llm = SimpleNamespace(invoke=lambda messages: responses.pop(0))

    messages = [HumanMessage(content="hi")]
    output = service._invoke_agent(messages)

    assert output == "done"
    tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert "工具执行失败" in tool_messages[0].content
    assert "call_1" in tool_messages[0].tool_call_id


def test_multiply_tool_rejects_bad_args_as_string():
    tool = MultiplyTool()
    assert tool.invoke({"a": 3, "b": 4}) == 12
    # 非法类型/缺参在两种路径下都返回错误文本而非抛异常：
    # args_schema 会拦住大部分（异常由 Agent 循环兜底），_run 自身的守卫覆盖剩余情况
    result = tool._run(a=True, b=2)   # 布尔是 int 子类，显式拒绝
    assert isinstance(result, str) and result.startswith("错误")
    result = tool._run(a=None, b=2)   # 缺参数
    assert isinstance(result, str) and "缺少参数" in result


def test_bge_reranker_instance_is_cached():
    import os as _os
    _os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

    calls = []

    class FakeReranker:
        def __init__(self, *args, **kwargs):
            calls.append(1)

        def compute_score(self, pairs):
            return [1.0] * len(pairs)

    fake_module = types.ModuleType("FlagEmbedding")
    fake_module.FlagReranker = FakeReranker
    sys.modules["FlagEmbedding"] = fake_module
    try:
        service = DocumentIndexService()
        docs = [Document(page_content=f"文档{i}", metadata={}) for i in range(6)]
        service._rerank_with_bge("query", docs)
        service._rerank_with_bge("query", docs)
        assert len(calls) == 1, "BGE 模型应只实例化一次"
        assert docs[0].metadata["score"] == 1.0
    finally:
        sys.modules.pop("FlagEmbedding", None)


def test_get_context_supplements_builtin_knowledge():
    class FakeBuiltin:
        def retrieve(self, query, k=3, category=None):
            return [{
                "title": "RAG 基础",
                "category": "agent_rag",
                "content": "RAG 是检索增强生成，先检索再生成。",
                "score": 0.8,
            }]

    service = DocumentIndexService(builtin_knowledge_service=FakeBuiltin())
    service._load_conversation_db = lambda conversation_id: None
    service._load_public_retriever = lambda: None

    result = service.get_context_with_details("conv_x", "什么是 RAG")

    # 知识库现在是常驻补充：会话/公共索引落空时也能拿到知识库内容
    assert "检索增强生成" in result["context"]
    assert result["documents"][0]["source_name"] == "RAG 基础"
