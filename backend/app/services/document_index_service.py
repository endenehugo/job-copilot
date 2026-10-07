from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from typing import Any

from langchain_community.embeddings import dashscope
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.config import settings
from app.utils import ResourceUtils

logger = logging.getLogger(__name__)


@dataclass
class DocumentIndexService:
    builtin_knowledge_service: Any | None = None
    _bm25_indexes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.embeddings = None
        self.public_db = None
        self._bge_reranker = None

    # ============================================================
    # 索引构建
    # ============================================================

    def rebuild_conversation_index(self, conversation_id: str, documents: list[dict]) -> dict:
        index_dir = self._get_conversation_index_dir(conversation_id)
        tmp_dir = index_dir + ".tmp"
        texts = []
        metadatas = []

        for document in documents:
            parsed_text_path = document.get("parsed_text_path")
            if not parsed_text_path or not os.path.exists(parsed_text_path):
                continue
            with open(parsed_text_path, "r", encoding="utf-8") as text_file:
                content = text_file.read().strip()
            if not content:
                continue

            for chunk_index, chunk in enumerate(self._chunk_text(content)):
                texts.append(self._build_index_text(document, chunk))
                metadatas.append({
                    "conversation_id": conversation_id,
                    "document_id": document.get("document_id"),
                    "chunk_index": chunk_index,
                    "original_name": document.get("original_name", ""),
                })

        # 先把新索引写到临时目录，构建成功后再替换旧目录。
        # 旧版先删后建：embedding API 一失败旧索引就没了，会话检索瘫痪到重新上传。
        if os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir)

        if not texts:
            if os.path.isdir(index_dir):
                shutil.rmtree(index_dir)
            os.makedirs(index_dir, exist_ok=True)
            self._bm25_indexes.pop(conversation_id, None)
            return {"chunk_count": 0}

        try:
            db = FAISS.from_texts(texts, self._ensure_embeddings(), metadatas=metadatas)
            db.save_local(tmp_dir)
        except Exception:
            if os.path.isdir(tmp_dir):
                shutil.rmtree(tmp_dir)
            raise

        if os.path.isdir(index_dir):
            shutil.rmtree(index_dir)
        os.rename(tmp_dir, index_dir)
        # 清理缓存的 BM25 索引，重建时重新生成
        self._bm25_indexes.pop(conversation_id, None)
        return {"chunk_count": len(texts)}

    # ============================================================
    # 上下文检索入口（含混合检索 + Reranking）
    # ============================================================

    def get_context(self, conversation_id: str, query: str, limit: int = 4) -> str:
        """获取检索上下文。

        策略：会话文档（混合检索 + Reranking）→ 公共全局索引，
        内置知识库作为常驻补充（Top-2）——聊天中询问面试知识时，
        AI 可结合知识库与自身知识作答。
        """
        docs = []
        conversation_db = self._load_conversation_db(conversation_id)
        if conversation_db is not None:
            docs = self._hybrid_retrieve(conversation_db, conversation_id, query, limit)

        if not docs:
            public_retriever = self._load_public_retriever()
            if public_retriever is not None:
                docs = public_retriever.get_relevant_documents(query)

        docs = self._supplement_with_builtin_knowledge(docs, query)
        return "\n\n".join(doc.page_content for doc in docs)

    def get_context_with_details(self, conversation_id: str, query: str, limit: int = 4) -> dict:
        """获取检索上下文及详细引用信息。"""
        docs = []
        source = ""
        conversation_db = self._load_conversation_db(conversation_id)
        if conversation_db is not None:
            docs = self._hybrid_retrieve(conversation_db, conversation_id, query, limit)
            source = "conversation"

        if not docs:
            public_retriever = self._load_public_retriever()
            if public_retriever is not None:
                docs = public_retriever.get_relevant_documents(query)
                source = "public"

        docs = self._supplement_with_builtin_knowledge(docs, query)

        return {
            "context": "\n\n".join(doc.page_content for doc in docs),
            "source": source,
            "documents": [
                {
                    "content": doc.page_content,
                    "source_name": doc.metadata.get("original_name", "未知文档"),
                    "score": doc.metadata.get("score"),
                }
                for doc in docs
            ],
        }

    def _supplement_with_builtin_knowledge(self, docs: list[Document], query: str) -> list[Document]:
        """内置知识库常驻补充：无论会话/公共索引是否命中，都追加最相关的 Top-2 条目。

        知识库条目带标题作为来源名，供引用与来源面板展示。
        """
        if self.builtin_knowledge_service is None:
            return docs

        results = self.builtin_knowledge_service.retrieve(query, k=2)
        if not results:
            return docs

        kb_docs = [
            Document(
                page_content=item.get("content", ""),
                metadata={
                    "original_name": item.get("title") or "内置知识库",
                    "source": "builtin_knowledge_base",
                    "score": item.get("score"),
                },
            )
            for item in results
        ]
        return docs + kb_docs

    # ============================================================
    # 混合检索（向量检索 + BM25 关键词检索 + Reranking）
    # ============================================================

    def _hybrid_retrieve(self, conversation_db, conversation_id: str, query: str, limit: int) -> list[Document]:
        """混合检索：向量检索 + BM25 关键词检索 → 合并 → Reranking。"""
        # 第一路：向量相似度检索（阈值优先，相似度兜底）
        vector_docs = self._search_conversation_docs(conversation_db, query, limit=limit * 2)

        # 第二路：BM25 关键词检索
        bm25_docs = self._bm25_retrieve(conversation_id, query, k=limit * 2)

        # 合并去重（按 page_content 去重）
        seen_contents = set()
        merged_docs = []
        for doc in vector_docs + bm25_docs:
            if doc.page_content not in seen_contents:
                seen_contents.add(doc.page_content)
                merged_docs.append(doc)

        if not merged_docs:
            return []

        # Reranking：用 LLM 对合并结果重新排序
        ranked_docs = self._rerank_docs(query, merged_docs, top_k=limit)
        return ranked_docs

    def _search_conversation_docs(self, conversation_db, query: str, limit: int) -> list[Document]:
        """向量检索：两级策略（阈值检索 + 相似度兜底）。"""
        threshold_retriever = conversation_db.as_retriever(
            search_type="similarity_score_threshold",
            search_kwargs={"k": limit, "score_threshold": 0.35},
        )
        docs = threshold_retriever.get_relevant_documents(query)
        if docs:
            return docs

        # 文件概述类问题与超短文本的向量相似度常常偏低，兜底返回最相关分片
        fallback_retriever = conversation_db.as_retriever(
            search_type="similarity",
            search_kwargs={"k": limit},
        )
        return fallback_retriever.get_relevant_documents(query)

    # ============================================================
    # BM25 关键词检索
    # ============================================================

    def _bm25_retrieve(self, conversation_id: str, query: str, k: int = 4) -> list[Document]:
        """BM25 关键词检索（基于 rank_bm25）。"""
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            logger.warning("rank_bm25 未安装，跳过 BM25 检索。请执行: pip install rank_bm25")
            return []

        bm25_data = self._get_or_build_bm25_index(conversation_id)
        if bm25_data is None:
            return []

        tokenized_query = self._tokenize(query)
        if not tokenized_query:
            return []

        scores = bm25_data["bm25"].get_scores(tokenized_query)
        doc_texts = bm25_data["texts"]
        metadatas = bm25_data["metadatas"]

        # 按 BM25 得分排序，取 top-k（score 为 0 说明词完全没命中，直接丢弃）
        indexed = sorted(enumerate(scores), key=lambda item: item[1], reverse=True)
        score_by_idx = dict(indexed)
        top_indices = [idx for idx, score in indexed[:k] if score > 0]

        docs = []
        for idx in top_indices:
            # Document 不允许动态挂属性，得分统一放进 metadata["score"]
            metadata = dict(metadatas[idx])
            metadata["score"] = float(score_by_idx.get(idx, 0.0))
            docs.append(Document(
                page_content=doc_texts[idx],
                metadata=metadata,
            ))
        return docs

    def _get_or_build_bm25_index(self, conversation_id: str) -> dict | None:
        """获取或构建 BM25 索引（缓存到实例变量）。"""
        if conversation_id in self._bm25_indexes:
            return self._bm25_indexes[conversation_id]

        # 从 FAISS 索引文件中读取存储的文本
        index_dir = self._get_conversation_index_dir(conversation_id)
        if not os.path.isdir(index_dir):
            return None

        try:
            import pickle
            texts = []
            metadatas = []
            # FAISS 持久化时会在同一目录生成 index.pkl
            store_path = os.path.join(index_dir, "index.pkl")
            if os.path.exists(store_path):
                with open(store_path, "rb") as f:
                    store_data = pickle.load(f)
                for doc in self._extract_store_documents(store_data):
                    if isinstance(doc, dict):
                        page_content = doc.get("page_content", "")
                        metadata = doc.get("metadata", {})
                    else:
                        page_content = getattr(doc, "page_content", "")
                        metadata = getattr(doc, "metadata", {}) or {}
                    if page_content:
                        texts.append(page_content)
                        metadatas.append(metadata)

            if not texts:
                logger.warning("无法从 index.pkl 加载文本，跳过 BM25 索引构建")
                return None

            from rank_bm25 import BM25Okapi
            tokenized_corpus = [self._tokenize(t) for t in texts]
            bm25 = BM25Okapi(tokenized_corpus)
            self._bm25_indexes[conversation_id] = {
                "bm25": bm25,
                "texts": texts,
                "metadatas": metadatas,
            }
            return self._bm25_indexes[conversation_id]
        except Exception as exc:
            logger.warning("构建 BM25 索引失败: %s", exc)
            return None

    @staticmethod
    def _extract_store_documents(store_data: Any) -> list[Any]:
        """从 FAISS save_local 的序列化数据中提取文档对象列表。

        index.pkl 的实际结构是 (docstore, index_to_docstore_id) 元组，
        文档本体存放在 InMemoryDocstore 的 _dict 中；同时兼容旧的
        文档列表 / 单个字典两种序列化形态。
        """
        if isinstance(store_data, tuple) and len(store_data) == 2:
            raw_docs = getattr(store_data[0], "_dict", None)
            return list(raw_docs.values()) if isinstance(raw_docs, dict) else []
        if isinstance(store_data, list):
            return store_data
        if isinstance(store_data, dict):
            return [store_data]
        return []

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """简单中文分词（按字和英文单词切分，用于 BM25）。"""
        import re
        # 中文按字切分，英文按单词切分
        tokens = re.findall(r"[一-鿿]|[a-zA-Z0-9]+", text.lower())
        return tokens

    # ============================================================
    # Reranking（基于 LLM 的语义重排序）
    # ============================================================

    def _rerank_docs(self, query: str, docs: list[Document], top_k: int = 4) -> list[Document]:
        """使用 LLM 对检索结果进行重排序。"""
        if len(docs) <= top_k:
            # 结果数量已经少于需求，按原有顺序返回
            return docs[:top_k]

        try:
            # 方案一：尝试使用 BGE Reranker（需安装 FlagEmbedding）
            reranked = self._rerank_with_bge(query, docs)
            if reranked is not None:
                return reranked[:top_k]
        except Exception:
            logger.debug("BGE Reranker 不可用，降级到 LLM Reranker")

        # 方案二：使用 LLM 进行重排序
        return self._rerank_with_llm(query, docs, top_k)

    def _rerank_with_bge(self, query: str, docs: list[Document]) -> list[Document] | None:
        """使用 BGE Reranker 模型进行重排序（需要 FlagEmbedding 库）。

        模型实例懒加载后缓存在服务实例上：旧版每次检索都重新加载一次
        数百 MB 的模型，是检索延迟的主要浪费点。
        """
        try:
            from FlagEmbedding import FlagReranker
        except ImportError:
            logger.debug("FlagEmbedding 未安装，跳过 BGE Reranker")
            return None
        try:
            if self._bge_reranker is None:
                self._bge_reranker = FlagReranker("BAAI/bge-reranker-v2-m3", use_fp16=False)
            pairs = [[query, doc.page_content] for doc in docs]
            scores = self._bge_reranker.compute_score(pairs)
            ranked = sorted(zip(docs, scores), key=lambda x: x[1], reverse=True)
            for doc, score in ranked:
                doc.metadata["score"] = float(score)
            return [doc for doc, _ in ranked]
        except Exception as exc:
            logger.warning("BGE Reranker 调用失败: %s", exc)
            return None

    def _rerank_with_llm(self, query: str, docs: list[Document], top_k: int = 4) -> list[Document]:
        """使用 LLM（Qwen）对文档进行相关性评分和重排序。"""
        from langchain_community.chat_models import ChatTongyi

        llm = ChatTongyi(
            model="qwen-plus",
            temperature=0.1,
            top_p=0.5,
        )

        # 分批评分，每批最多 10 个文档
        scored_docs = []
        batch_size = 10
        for i in range(0, len(docs), batch_size):
            batch = docs[i:i + batch_size]
            batch_scores = self._score_batch_with_llm(llm, query, batch)
            for doc, score in zip(batch, batch_scores):
                doc.metadata["score"] = score
                scored_docs.append((doc, score))

        # 按得分降序排列
        scored_docs.sort(key=lambda x: x[1], reverse=True)
        return [doc for doc, _ in scored_docs[:top_k]]

    def _score_batch_with_llm(self, llm, query: str, batch: list[Document]) -> list[float]:
        """用 LLM 评估一批文档与查询的相关性，返回 0-10 的分数列表。"""
        scores = []
        for doc in batch:
            try:
                prompt = (
                    f"你是一个文档相关性评估专家。请判断以下文档内容与用户问题是否相关。\n\n"
                    f"用户问题：{query}\n\n"
                    f"文档内容：{doc.page_content[:500]}\n\n"
                    f"请只输出一个 0-10 的整数分数（0=完全不相关，10=高度相关），不要输出其他内容："
                )
                result = llm.invoke(prompt).content.strip()
                score = float(result)
                if score < 0 or score > 10:
                    score = 5.0
            except Exception:
                score = 5.0  # 默认中等相关
            scores.append(score)
        return scores

    # ============================================================
    # 工具方法
    # ============================================================

    @staticmethod
    def _build_index_text(document: dict, chunk: str) -> str:
        original_name = (document.get("original_name") or "").strip()
        if not original_name:
            return chunk
        return f"文件名：{original_name}\n{chunk}"

    def delete_conversation_index(self, conversation_id: str) -> None:
        index_dir = self._get_conversation_index_dir(conversation_id)
        if os.path.isdir(index_dir):
            shutil.rmtree(index_dir)
        self._bm25_indexes.pop(conversation_id, None)

    def _chunk_text(self, content: str) -> list[str]:
        """语义切分：使用 RecursiveCharacterTextSplitter 保留自然边界。"""
        chunk_size = settings.conversation_index_chunk_size
        chunk_overlap = settings.conversation_index_chunk_overlap
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", "。", "；", "，", " ", ""],
            keep_separator=False,
        )
        return splitter.split_text(content)

    def _ensure_embeddings(self):
        if self.embeddings is None:
            self.embeddings = dashscope.DashScopeEmbeddings(model="text-embedding-v3")
        return self.embeddings

    def _load_conversation_db(self, conversation_id: str):
        index_dir = self._get_conversation_index_dir(conversation_id)
        if not os.path.isdir(index_dir):
            return None
        if not any(name.endswith(".faiss") for name in os.listdir(index_dir)):
            return None
        return FAISS.load_local(
            index_dir,
            self._ensure_embeddings(),
            allow_dangerous_deserialization=True,
        )

    def _load_public_retriever(self):
        public_path = ResourceUtils.get_resource_path("faiss_index")
        if not os.path.isdir(public_path):
            return None
        if self.public_db is None:
            self.public_db = FAISS.load_local(
                public_path,
                self._ensure_embeddings(),
                allow_dangerous_deserialization=True,
            )
        return self.public_db.as_retriever(
            search_type="similarity_score_threshold",
            search_kwargs={"k": 3, "score_threshold": 0.4},
        )

    @staticmethod
    def _get_conversation_index_dir(conversation_id: str) -> str:
        return ResourceUtils.get_resource_path(os.path.join("faiss_index_uploads", conversation_id))