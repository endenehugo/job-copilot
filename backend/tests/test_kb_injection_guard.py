"""内置知识库注入的边界保护（2026-10-22）。

背景：知识库原先是"无论命中与否都追加 Top-2"，而相关性门槛只有 0.3，
实测问「介绍本 Job Copilot 项目」会注入「Flask 项目结构模式」（真实相关性 0.336），
写简历时这些通用知识被当成了用户经历，生成了用户从未提供过的项目。
这里锁住三条规则：相关性门槛、简历任务不注入、上下文按来源分区。
"""
import unittest

from langchain_core.documents import Document

from app.services.builtin_knowledge_service import BuiltinKnowledgeService
from app.services.document_index_service import DocumentIndexService


class FakeBuiltin:
    """假内置知识库：记录调用，返回预设条目。"""

    def __init__(self, items):
        self.items = items
        self.calls = []

    def retrieve(self, query, k=3, category=None, score_threshold=None):
        self.calls.append({"query": query, "k": k})
        return self.items[:k]


def _doc(content, source="", title=""):
    metadata = {}
    if source:
        metadata["source"] = source
    if title:
        # retrieve() 读 title（知识库原始元数据），_format_context() 读 original_name
        metadata["title"] = title
        metadata["original_name"] = title
    return Document(page_content=content, metadata=metadata)


def _service_with(items):
    fake = FakeBuiltin(items)
    service = DocumentIndexService(builtin_knowledge_service=fake)
    service._load_conversation_db = lambda conversation_id: None
    service._load_public_retriever = lambda: None
    return service, fake


KB_ITEM = {"title": "Flask 项目结构模式", "category": "flask_engineering",
           "content": "推荐 Flask 项目结构：app/、handler/、services/", "score": 0.9}

RESUME_QUERIES = [
    "帮我写一份前端工程师的简历",
    "请生成一份 resume",
    "改一下我的履历",
]


class ResumeTaskGuardTest(unittest.TestCase):
    def test_resume_task_skips_builtin_knowledge(self):
        """写简历时不注入任何内置知识库条目——它们是编造素材的来源。"""
        for query in RESUME_QUERIES:
            service, fake = _service_with([KB_ITEM])
            result = service.get_context_with_details("conv_1", query)
            self.assertEqual(result["documents"], [], f"{query} 不应注入知识库")
            self.assertEqual(fake.calls, [], f"{query} 不应触发知识库检索，实际调用了 {fake.calls}")

    def test_non_resume_task_still_gets_knowledge(self):
        """非简历任务保留知识库补充，别把功能打死了。"""
        service, fake = _service_with([KB_ITEM])
        result = service.get_context_with_details("conv_1", "什么是 RAG 检索增强生成")
        self.assertEqual(len(result["documents"]), 1)
        self.assertEqual(result["documents"][0]["source_name"], "Flask 项目结构模式")
        self.assertTrue(fake.calls)


class ContextPartitionTest(unittest.TestCase):
    def test_context_separates_user_docs_and_knowledge(self):
        """用户文档和内置知识库必须分区，且知识库那段要写明"不是用户经历"。"""
        service = DocumentIndexService()
        context = service._format_context([
            _doc("候选人的真实项目：校园二手交易平台", source="upload", title="我的简历.pdf"),
            _doc("RAG 是检索增强生成", source="builtin_knowledge_base", title="RAG 基础"),
        ])
        self.assertIn("用户上传的文档", context)
        self.assertIn("内置知识库参考", context)
        self.assertIn("不是用户的真实经历", context)
        self.assertIn("严禁把其中的项目、公司、数字、技术栈", context)
        # 用户文档段落必须排在知识库之前
        self.assertLess(context.index("校园二手交易平台"), context.index("RAG 是检索增强生成"))

    def test_only_user_docs_still_labelled(self):
        """只有用户文档时也要带"事实来源"标题，让模型知道可直接引用。"""
        service = DocumentIndexService()
        context = service._format_context([_doc("候选人做过二手交易平台", source="upload")])
        self.assertIn("用户上传的文档", context)
        self.assertNotIn("内置知识库参考", context)

    def test_empty_docs_returns_empty_string(self):
        service = DocumentIndexService()
        self.assertEqual(service._format_context([]), "")


class FakeDb:
    def __init__(self, pairs):
        self.pairs = pairs

    def similarity_search_with_relevance_scores(self, query, k=4):
        return self.pairs[:k]


class BuiltinKnowledgeThresholdTest(unittest.TestCase):
    def test_weak_hits_are_dropped_and_score_is_real(self):
        """0.34 这类弱相关条目要被默认门槛挡掉，且返回的 score 是真实值（不再是恒为 0）。"""
        service = BuiltinKnowledgeService()
        service._load_db = lambda: FakeDb([
            (_doc("弱相关：Flask 项目结构", title="Flask 项目结构模式"), 0.337),
            (_doc("强相关：RAG 检索增强生成", title="RAG 基础"), 0.82),
        ])

        results = service.retrieve("介绍本 Job Copilot 项目", k=2)

        self.assertEqual([r["title"] for r in results], ["RAG 基础"])
        self.assertAlmostEqual(results[0]["score"], 0.82, places=3)

    def test_caller_can_relax_threshold(self):
        """用户主动搜知识库时可放宽门槛。"""
        service = BuiltinKnowledgeService()
        service._load_db = lambda: FakeDb([
            (_doc("弱相关：Flask 项目结构", title="Flask 项目结构模式"), 0.337),
        ])

        self.assertEqual(service.retrieve("面试", k=1), [])
        relaxed = service.retrieve("面试", k=1, score_threshold=0.3)
        self.assertEqual(len(relaxed), 1)

    def test_retrieve_failure_does_not_raise(self):
        """索引异常不能让整条问答链路挂掉。"""

        class BoomDb:
            def similarity_search_with_relevance_scores(self, query, k=4):
                raise RuntimeError("index corrupted")

        service = BuiltinKnowledgeService()
        service._load_db = lambda: BoomDb()
        self.assertEqual(service.retrieve("任意查询"), [])


if __name__ == "__main__":
    unittest.main()
