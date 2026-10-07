import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.embeddings import Embeddings

from app.services.document_index_service import DocumentIndexService


class DeterministicFakeEmbeddings(Embeddings):
    """确定性假向量，让 FAISS 单测不依赖 DashScope 网络。"""

    @staticmethod
    def _embed(text: str) -> list[float]:
        seed = sum(ord(char) for char in text) or 1
        return [float((seed * (index + 1)) % 17) for index in range(8)]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class FakeRetriever:
    def __init__(self, docs):
        self.docs = docs

    def get_relevant_documents(self, query):
        return self.docs


class FakeConversationDb:
    def __init__(self, threshold_docs, fallback_docs):
        self.threshold_docs = threshold_docs
        self.fallback_docs = fallback_docs
        self.calls = []

    def as_retriever(self, search_type, search_kwargs):
        self.calls.append((search_type, search_kwargs))
        if search_type == "similarity_score_threshold":
            return FakeRetriever(self.threshold_docs)
        if search_type == "similarity":
            return FakeRetriever(self.fallback_docs)
        raise AssertionError(f"unexpected search_type: {search_type}")


class DocumentIndexServiceTest(unittest.TestCase):
    def test_get_context_falls_back_to_similarity_when_threshold_returns_nothing(self):
        service = DocumentIndexService()
        conversation_db = FakeConversationDb(
            threshold_docs=[],
            fallback_docs=[SimpleNamespace(page_content="test.txt里面有说明")],
        )
        service._load_conversation_db = lambda conversation_id: conversation_db
        service._load_public_retriever = lambda: None

        context = service.get_context("conv_1", "test.txt里面有什么")

        self.assertEqual(context, "test.txt里面有说明")
        # 混合检索下向量路取 limit*2=8 条候选参与合并重排
        self.assertEqual(
            conversation_db.calls,
            [
                ("similarity_score_threshold", {"k": 8, "score_threshold": 0.35}),
                ("similarity", {"k": 8}),
            ],
        )

    def test_rebuild_conversation_index_includes_original_name_in_indexed_text(self):
        service = DocumentIndexService()
        with tempfile.TemporaryDirectory() as parsed_dir:
            parsed_text_path = os.path.join(parsed_dir, "doc_1.txt")
            with open(parsed_text_path, "w", encoding="utf-8") as parsed_file:
                parsed_file.write("123")
            documents = [{
                "document_id": "doc_1",
                "original_name": "test.docx",
                "parsed_text_path": parsed_text_path,
            }]

            with patch("app.services.document_index_service.FAISS.from_texts") as mocked_from_texts, patch.object(service, "_ensure_embeddings", return_value="embeddings"), patch.object(service, "_get_conversation_index_dir", return_value=os.path.join(parsed_dir, "index")), patch.object(service, "_chunk_text", return_value=["123"]):
                mocked_from_texts.return_value = SimpleNamespace(save_local=lambda index_dir: None)

                service.rebuild_conversation_index("conv_test", documents)

        indexed_texts = mocked_from_texts.call_args.args[0]
        self.assertEqual(indexed_texts, ["文件名：test.docx\n123"])

    def test_bm25_retrieve_ranks_keyword_hits(self):
        service = DocumentIndexService()
        from rank_bm25 import BM25Okapi
        # 3 篇起才是有效语料：Okapi IDF 在 N=2、df=1 时恒为 0
        texts = [
            "文件名：a.txt\nPython 装饰器是高阶函数",
            "文件名：b.txt\n今天天气不错",
            "文件名：c.txt\n数据库索引使用 B+ 树",
        ]
        service._bm25_indexes["conv_1"] = {
            "bm25": BM25Okapi([service._tokenize(text) for text in texts]),
            "texts": texts,
            "metadatas": [{"original_name": "a.txt"}, {"original_name": "b.txt"}, {"original_name": "c.txt"}],
        }

        docs = service._bm25_retrieve("conv_1", "装饰器", k=2)

        self.assertEqual(len(docs), 1)
        self.assertIn("装饰器", docs[0].page_content)
        self.assertEqual(docs[0].metadata["original_name"], "a.txt")
        self.assertGreater(docs[0].metadata["score"], 0.0)

    def test_get_or_build_bm25_index_reads_faiss_store(self):
        from langchain_community.vectorstores import FAISS
        service = DocumentIndexService()
        with tempfile.TemporaryDirectory() as index_dir:
            FAISS.from_texts(
                [
                    "Python 装饰器本质是高阶函数",
                    "Redis 支持多种数据结构",
                    "MySQL 事务保证原子性",
                ],
                DeterministicFakeEmbeddings(),
                metadatas=[
                    {"original_name": "a.txt"},
                    {"original_name": "b.txt"},
                    {"original_name": "c.txt"},
                ],
            ).save_local(index_dir)
            service._get_conversation_index_dir = lambda conversation_id: index_dir

            bm25_data = service._get_or_build_bm25_index("conv_test")

            self.assertIsNotNone(bm25_data)
            self.assertEqual(len(bm25_data["texts"]), 3)
            self.assertEqual(bm25_data["metadatas"][0]["original_name"], "a.txt")

            docs = service._bm25_retrieve("conv_test", "装饰器", k=3)

        self.assertEqual([doc.metadata["original_name"] for doc in docs], ["a.txt"])


if __name__ == "__main__":
    unittest.main()