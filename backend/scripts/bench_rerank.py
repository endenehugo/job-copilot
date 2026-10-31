"""Rerank 策略对比基准：RRF 融合 vs LLM 逐篇打分。

用法：
    cd backend && D:/conda/python.exe scripts/bench_rerank.py

输出每个 query 下两种策略的 Top-K 排序、耗时、集合重合度与顺序一致度。
不联网修改任何业务数据，只读索引。
"""

from __future__ import annotations

import copy
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.documents import Document

from app.services.builtin_knowledge_service import BuiltinKnowledgeService
from app.services.document_index_service import DocumentIndexService

CONVERSATION_ID = "conv_20260712072752_db28e8"

QUERIES = [
    "彭禹弘的教育背景是什么",
    "他会哪些编程语言",
    "潮汕牛肉丸的价格是多少",
    "有哪些电商产品在售",
    "简历里有哪些项目经历",
    "联系方式是什么",
]

TOP_K = 4
RRF_K = 60


def rrf_fuse(lists: list[list[Document]], k: int = RRF_K, top_k: int = TOP_K) -> list[Document]:
    """倒排秩融合：score(d) = Σ 1/(k + rank)。"""
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
        out.append(doc)
    return out


def merge(vector_docs: list[Document], bm25_docs: list[Document]) -> list[Document]:
    seen = set()
    merged = []
    for doc in vector_docs + bm25_docs:
        if doc.page_content not in seen:
            seen.add(doc.page_content)
            merged.append(doc)
    return merged


def topk_key(docs: list[Document]) -> list[str]:
    return [d.page_content[:60] for d in docs[:TOP_K]]


def jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if (sa | sb) else 1.0


def order_agreement(a: list[str], b: list[str]) -> float:
    """共同元素的相对顺序一致比例（简易 Kendall 变体）。"""
    common = [x for x in a if x in set(b)]
    if len(common) < 2:
        return 1.0 if common else 0.0
    pos_b = {x: i for i, x in enumerate(b)}
    pairs = 0
    agree = 0
    for i in range(len(common)):
        for j in range(i + 1, len(common)):
            pairs += 1
            if pos_b[common[i]] < pos_b[common[j]]:
                agree += 1
    return agree / pairs


def preview(text: str, n: int = 50) -> str:
    return text.replace("\n", " ")[:n]


def main() -> None:
    svc = DocumentIndexService(builtin_knowledge_service=BuiltinKnowledgeService())
    db = svc._load_conversation_db(CONVERSATION_ID)
    if db is None:
        print(f"无法加载会话索引：{CONVERSATION_ID}")
        return

    print(f"会话：{CONVERSATION_ID}")
    print(f"Top-K={TOP_K}  RRF k={RRF_K}\n")

    total_rrf = 0.0
    total_llm = 0.0
    jaccards = []
    orders = []

    for query in QUERIES:
        vector_docs = svc._search_conversation_docs(db, query, limit=TOP_K * 2)
        bm25_docs = svc._bm25_retrieve(CONVERSATION_ID, query, k=TOP_K * 2)
        merged = merge(vector_docs, bm25_docs)

        print("=" * 78)
        print(f"Q: {query}")
        print(f"  召回：向量 {len(vector_docs)} 篇 + BM25 {len(bm25_docs)} 篇 → 合并 {len(merged)} 篇")

        if len(merged) <= TOP_K:
            print("  合并结果 <= Top-K，两种策略都不会触发重排，跳过")
            continue

        t0 = time.time()
        rrf_docs = rrf_fuse([vector_docs, bm25_docs], top_k=TOP_K)
        t_rrf = time.time() - t0

        t0 = time.time()
        llm_docs = svc._rerank_with_llm(query, copy.deepcopy(merged), TOP_K)
        t_llm = time.time() - t0

        total_rrf += t_rrf
        total_llm += t_llm

        a, b = topk_key(rrf_docs), topk_key(llm_docs)
        j = jaccard(a, b)
        o = order_agreement(a, b)
        jaccards.append(j)
        orders.append(o)

        print(f"  RRF  ({t_rrf*1000:7.1f} ms)")
        for i, d in enumerate(rrf_docs):
            print(f"    {i+1}. [{d.metadata.get('score'):.4f}] {preview(d.page_content)}")
        print(f"  LLM  ({t_llm*1000:7.1f} ms)")
        for i, d in enumerate(llm_docs):
            print(f"    {i+1}. [{d.metadata.get('score')}] {preview(d.page_content)}")
        print(f"  → Top-{TOP_K} 集合重合 Jaccard = {j:.2f}　顺序一致 = {o:.2f}")

    print("\n" + "=" * 78)
    print("汇总")
    print(f"  RRF 总耗时 {total_rrf*1000:.0f} ms　LLM 总耗时 {total_llm*1000:.0f} ms"
          f"　倍差 {total_llm/total_rrf:.0f}x" if total_rrf else "")
    if jaccards:
        print(f"  平均集合重合 {sum(jaccards)/len(jaccards):.2f}　平均顺序一致 {sum(orders)/len(orders):.2f}")


if __name__ == "__main__":
    main()
