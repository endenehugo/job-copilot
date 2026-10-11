"""T16：索引增量更新（A12）。

上传时只对新文档切分 + embedding 追加进现有索引；
无索引/追加失败退回全量重建；按 document_id 去重。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from langchain_community.vectorstores import FAISS
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


def _make_document(doc_id: str, content: str, parsed_dir: str) -> dict:
    parsed_text_path = os.path.join(parsed_dir, f"{doc_id}.txt")
    with open(parsed_text_path, "w", encoding="utf-8") as f:
        f.write(content)
    return {
        "document_id": doc_id,
        "original_name": f"{doc_id}.txt",
        "parsed_text_path": parsed_text_path,
    }


class IndexIncrementalTest(unittest.TestCase):
    def setUp(self):
        self.service = DocumentIndexService()
        self._tmp = tempfile.TemporaryDirectory()
        self.index_dir = os.path.join(self.tmp_root, "index")
        os.makedirs(self.index_dir, exist_ok=True)
        self.addCleanup(self._tmp.cleanup)

    @property
    def tmp_root(self):
        return self._tmp.name

    def _patch_service(self):
        patcher = patch.object(
            self.service,
            "_get_conversation_index_dir",
            return_value=self.index_dir,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        emb_patcher = patch.object(
            self.service, "_ensure_embeddings", return_value=DeterministicFakeEmbeddings()
        )
        emb_patcher.start()
        self.addCleanup(emb_patcher.stop)
        chunk_patcher = patch.object(self.service, "_chunk_text", side_effect=lambda text: [text])
        chunk_patcher.start()
        self.addCleanup(chunk_patcher.stop)

    def _build_existing_index(self, doc_ids):
        texts = [f"文件名：{d}.txt\n已有内容{d}" for d in doc_ids]
        metas = [
            {"conversation_id": "conv_test", "document_id": d, "chunk_index": 0, "original_name": f"{d}.txt"}
            for d in doc_ids
        ]
        FAISS.from_texts(texts, DeterministicFakeEmbeddings(), metadatas=metas).save_local(self.index_dir)

    def _stored_doc_ids(self):
        db = self.service._load_conversation_db("conv_test")
        return {
            (getattr(doc, "metadata", None) or {}).get("document_id")
            for doc in db.docstore._dict.values()
        }

    def test_incremental_adds_only_new_chunks(self):
        self._patch_service()
        self._build_existing_index(["doc_1", "doc_2", "doc_3"])
        new_doc = _make_document("doc_4", "全新内容", self.tmp_root)
        all_docs = [_make_document(d, f"已有内容{d}", self.tmp_root) for d in ["doc_1", "doc_2", "doc_3"]] + [new_doc]

        result = self.service.add_document_to_index("conv_test", new_doc, all_docs)

        self.assertEqual(result["mode"], "incremental")
        self.assertEqual(result["chunk_count"], 1)
        self.assertEqual(self._stored_doc_ids(), {"doc_1", "doc_2", "doc_3", "doc_4"})

    def test_duplicate_document_is_skipped(self):
        self._patch_service()
        self._build_existing_index(["doc_1", "doc_2", "doc_3"])
        dup_doc = _make_document("doc_2", "重复上传的内容", self.tmp_root)

        result = self.service.add_document_to_index("conv_test", dup_doc, [])

        self.assertEqual(result["mode"], "deduped")
        self.assertEqual(result["chunk_count"], 0)
        self.assertEqual(self._stored_doc_ids(), {"doc_1", "doc_2", "doc_3"})

    def test_no_existing_index_falls_back_to_full_rebuild(self):
        self._patch_service()
        new_doc = _make_document("doc_1", "内容一", self.tmp_root)
        old_doc = _make_document("doc_0", "历史内容", self.tmp_root)

        result = self.service.add_document_to_index("conv_test", new_doc, [old_doc, new_doc])

        # 全量重建应包含全部文档，而不是只有新文档
        self.assertEqual(result["mode"], "full_rebuild")
        self.assertEqual(result["chunk_count"], 2)
        self.assertEqual(self._stored_doc_ids(), {"doc_0", "doc_1"})

    def test_add_texts_failure_falls_back_to_full_rebuild(self):
        self._patch_service()
        self._build_existing_index(["doc_1", "doc_2", "doc_3"])
        new_doc = _make_document("doc_4", "全新内容", self.tmp_root)
        all_docs = [_make_document(d, f"已有内容{d}", self.tmp_root) for d in ["doc_1", "doc_2", "doc_3"]] + [new_doc]

        with patch.object(FAISS, "add_texts", side_effect=RuntimeError("追加失败")):
            result = self.service.add_document_to_index("conv_test", new_doc, all_docs)

        self.assertEqual(result["mode"], "full_rebuild")
        self.assertEqual(self._stored_doc_ids(), {"doc_1", "doc_2", "doc_3", "doc_4"})


if __name__ == "__main__":
    unittest.main()
