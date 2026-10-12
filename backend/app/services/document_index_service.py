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
        texts, metadatas = self._build_chunks(conversation_id, documents)

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

    def add_document_to_index(self, conversation_id: str, document: dict, all_documents: list[dict]) -> dict:
        """A12 增量更新：上传时只对新文档切分 + embedding，追加进现有索引。

        - 已有索引 → FAISS.add_texts 追加，临时目录 + 原子替换（复用已付费的 embedding）
        - 无索引 / 现有索引损坏 / 追加失败 → 退回全量重建（用 all_documents）
        - 按 document_id 去重：重试上传不会把同一文档入库两遍
        删除文档仍走全量重建（删除语义下增量成本更高）。
        """
        texts, metadatas = self._build_chunks(conversation_id, [document])
        if not texts:
            return {"chunk_count": 0, "mode": "noop"}

        doc_id = document.get("document_id")
        try:
            db = self._load_conversation_db(conversation_id)
        except Exception:
            logger.warning("会话 %s 现有索引加载失败，退回全量重建", conversation_id, exc_info=True)
            db = None

        if db is not None:
            # docstore 是 InMemoryDocstore，_dict 里是已入库的 Document
            existing_ids = {
                (getattr(doc, "metadata", None) or {}).get("document_id")
                for doc in db.docstore._dict.values()
            }
            if doc_id and doc_id in existing_ids:
                logger.info("文档 %s 已在会话 %s 索引中，跳过重复入库", doc_id, conversation_id)
                return {"chunk_count": 0, "mode": "deduped"}
            try:
                db.add_texts(texts, metadatas=metadatas)
                index_dir = self._get_conversation_index_dir(conversation_id)
                tmp_dir = index_dir + ".tmp"
                if os.path.isdir(tmp_dir):
                    shutil.rmtree(tmp_dir)
                try:
                    db.save_local(tmp_dir)
                except Exception:
                    if os.path.isdir(tmp_dir):
                        shutil.rmtree(tmp_dir)
                    raise
                if os.path.isdir(index_dir):
                    shutil.rmtree(index_dir)
                os.rename(tmp_dir, index_dir)
                self._bm25_indexes.pop(conversation_id, None)
                return {"chunk_count": len(texts), "mode": "incremental"}
            except Exception:
                logger.warning("会话 %s 增量追加失败，退回全量重建", conversation_id, exc_info=True)

        result = self.rebuild_conversation_index(conversation_id, all_documents)
        result["mode"] = "full_rebuild"
        return result

    def _build_chunks(self, conversation_id: str, documents: list[dict]) -> tuple[list[str], list[dict]]:
        """文档列表 → (index texts, metadatas)。增量与全量共用。"""
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
        return texts, metadatas

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
                    # 三种策略的 score 语义不同（0-10 打分 / 序位 / RRF 融合分），
                    # 前端据此选择文案，避免把序位当相关性分数展示
                    "score_source": doc.metadata.get("score_source"),
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

        # Reranking：默认 listwise 单次调用排序，失败降级 RRF
        return self._rerank_docs(
            query, merged_docs, top_k=limit, lists=[vector_docs, bm25_docs]
        )

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

    def _rerank_docs(
        self,
        query: str,
        docs: list[Document],
        top_k: int = 4,
        lists: list[list[Document]] | None = None,
    ) -> list[Document]:
        """按配置策略重排检索结果。

        Args:
            lists: 各路召回的原始结果。RRF 需要按"在每路中的排名"融合，
                   仅凭合并后的 docs 无法还原排名，故由调用方传入。
        """
        if len(docs) <= top_k:
            # 结果数量已经少于需求，按原有顺序返回
            return docs[:top_k]

        strategy = (settings.rerank_strategy or "listwise").strip().lower()
        if strategy == "off":
            return docs[:top_k]
        if strategy == "rrf":
            return self._rrf_fuse(lists or [docs], top_k=top_k)

        try:
            # 方案一：BGE Reranker（需安装 FlagEmbedding），命中则优先用
            reranked = self._rerank_with_bge(query, docs)
            if reranked is not None:
                return reranked[:top_k]
        except Exception as exc:
            # 旧代码这里是 debug 级，生产环境完全看不到降级原因
            logger.warning("BGE Reranker 不可用，降级到 %s: %s", strategy, exc)

        # 方案二：LLM 重排
        try:
            if strategy == "per_doc":
                return self._rerank_with_llm(query, docs, top_k)
            return self._rerank_with_listwise(query, docs, top_k)
        except Exception as exc:
            logger.warning("LLM 重排失败，降级到 RRF: %s", exc)
            return self._rrf_fuse(lists or [docs], top_k=top_k)

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

    def _rerank_with_listwise(self, query: str, docs: list[Document], top_k: int = 4) -> list[Document]:
        """单次 LLM 调用直接输出排序结果（默认策略）。

        实测相对逐篇打分提速 5.8x（3.7s vs 21.4s），Top-4 重合 0.71。
        比"批量打分"更优的原因：批量打分会出现分数退化（多篇都给 8 分），
        排序退化为近似随机；直接要求输出编号顺序能强制产生严格序，
        且输出更短所以更快。
        """
        import json
        import re

        from app.core.llm_factory import build_chat_llm

        candidates = self._limit_candidates(docs)
        if len(candidates) <= top_k:
            return candidates[:top_k]

        llm = build_chat_llm(
            model=settings.llm_small_model,
            temperature=0.1,
            top_p=0.5,
        )

        char_limit = settings.llm_rerank_char_limit
        numbered = [f"[{i}] {doc.page_content[:char_limit]}" for i, doc in enumerate(candidates)]
        prompt = (
            "你是文档相关性排序专家。下面是用户问题和若干候选文档。\n\n"
            f"用户问题：{query}\n\n候选文档：\n" + "\n".join(numbered) + "\n\n"
            f"请按与用户问题的相关程度从高到低排序，输出最相关的 {top_k} 篇文档编号。"
            "只输出 JSON 数组，不要任何其他内容：\n[3, 1, 7, 2]"
        )

        raw = llm.invoke(prompt).content.strip()
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            raise ValueError(f"无法从输出中解析编号数组：{raw[:200]}")
        parsed = json.loads(match.group(0))

        ordered: list[int] = []
        seen: set[int] = set()
        for item in parsed:
            try:
                index = int(item)
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(candidates) and index not in seen:
                seen.add(index)
                ordered.append(index)
        # 模型漏给或给了非法编号时，按原召回顺序补齐
        for index in range(len(candidates)):
            if len(ordered) >= top_k:
                break
            if index not in seen:
                seen.add(index)
                ordered.append(index)

        ranked = []
        for rank, index in enumerate(ordered[:top_k]):
            doc = candidates[index]
            doc.metadata["score"] = float(top_k - rank)
            doc.metadata["score_source"] = "llm_listwise"
            ranked.append(doc)
        return ranked

    @staticmethod
    def _limit_candidates(docs: list[Document]) -> list[Document]:
        """控制参与 LLM 重排的文档数，避免 prompt 过长。"""
        limit = settings.llm_rerank_max_docs
        if limit and limit > 0 and len(docs) > limit:
            return docs[:limit]
        return docs

    @staticmethod
    def _rrf_fuse(lists: list[list[Document]], top_k: int = 4) -> list[Document]:
        """倒排秩融合（Reciprocal Rank Fusion），零 LLM 调用。

        仅在 LLM 重排失败或显式配置 rrf 时使用：它不看 query-doc 语义，
        完全依赖各路召回的排名，本项目 BM25 按字切分导致召回质量有限，
        因此不作为默认策略。
        """
        k = settings.rerank_rrf_k or 60
        scores: dict[str, float] = {}
        doc_by_content: dict[str, Document] = {}
        for docs in lists:
            for rank, doc in enumerate(docs):
                key = doc.page_content
                scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
                doc_by_content.setdefault(key, doc)

        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        out = []
        for key, score in ranked[:top_k]:
            doc = doc_by_content[key]
            doc.metadata["score"] = float(score)
            doc.metadata["score_source"] = "rrf"
            out.append(doc)
        return out

    def _rerank_with_llm(self, query: str, docs: list[Document], top_k: int = 4) -> list[Document]:
        """逐篇打分的 LLM 重排（per_doc 策略）。

        保留用于效果对比与回退：每篇文档一次 LLM 往返，文档多时很慢。
        """
        from app.core.llm_factory import build_chat_llm

        llm = build_chat_llm(
            model=settings.llm_small_model,
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
                scored_docs.append((doc, score))

        # 打分失败的（score 为 None）排到最后
        scored_docs.sort(key=lambda x: -1.0 if x[1] is None else x[1], reverse=True)
        ranked = []
        for doc, score in scored_docs[:top_k]:
            doc.metadata["score"] = score
            doc.metadata["score_source"] = "llm_per_doc"
            ranked.append(doc)
        return ranked

    def _score_batch_with_llm(self, llm, query: str, batch: list[Document]) -> list[float | None]:
        """用 LLM 评估一批文档与查询的相关性，返回 0-10 的分数列表。

        打分失败返回 None。旧代码返回 5.0 伪造一个"中等相关"，
        会让前端显示一个看起来合理、实际与相关性无关的假分数。
        """
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
                    score = None
            except Exception as exc:
                logger.warning("Rerank 打分失败: %s", exc)
                score = None
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