"""Rerank 策略三方对比：逐篇 LLM 打分 vs 单次批量打分 vs RRF 融合。

用法：
    cd backend && D:/conda/python.exe scripts/bench_rerank_v2.py

批量策略把 N 次串行 LLM 往返压成 1 次，用于验证"提速是否以精度为代价"。
"""

from __future__ import annotations

import copy
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_community.chat_models import ChatTongyi

from app.services.builtin_knowledge_service import BuiltinKnowledgeService
from app.services.document_index_service import DocumentIndexService

CONVERSATION_ID = "conv_20260712072752_db28e8"

QUERIES = [
    "彭禹弘的教育背景是什么",
    "潮汕牛肉丸的价格是多少",
    "有哪些电商产品在售",
    "简历里有哪些项目经历",
    "联系方式是什么",
]

TOP_K = 4
RRF_K = 60
CHARS_PER_DOC = 300


def build_llm() -> ChatTongyi:
    return ChatTongyi(model="qwen-plus", temperature=0.1, top_p=0.5)


def rrf_fuse(lists, k=RRF_K, top_k=TOP_K):
    scores, by_content = {}, {}
    for docs in lists:
        for rank, doc in enumerate(docs):
            key = doc.page_content
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            by_content.setdefault(key, doc)
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    out = []
    for key, score in ranked[:top_k]:
        doc = by_content[key]
        doc.metadata["score"] = float(score)
        out.append(doc)
    return out


def batch_score(llm, query, docs, top_k=TOP_K, char_limit=CHARS_PER_DOC):
    """单次 LLM 调用给所有候选文档打分。"""
    numbered = []
    for i, doc in enumerate(docs):
        numbered.append(f"[{i}] {doc.page_content[:char_limit]}")
    prompt = (
        "你是文档相关性评估专家。下面是用户问题和若干候选文档，"
        "请判断每篇文档与用户问题的相关程度。\n\n"
        f"用户问题：{query}\n\n候选文档：\n" + "\n".join(numbered) + "\n\n"
        "请为每篇文档打 0-10 的整数分（0=完全不相关，10=高度相关）。"
        '只输出 JSON 数组，不要任何其他内容：\n[{"id":0,"score":8}, ...]'
    )
    raw = llm.invoke(prompt).content.strip()
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        raise ValueError(f"无法从输出中解析 JSON：{raw[:200]}")
    items = json.loads(match.group(0))
    score_by_id = {}
    for item in items:
        try:
            score_by_id[int(item["id"])] = float(item["score"])
        except (KeyError, TypeError, ValueError):
            continue
    if not score_by_id:
        raise ValueError("解析出的分数为空")
    scored = []
    for i, doc in enumerate(docs):
        score = score_by_id.get(i)
        if score is None or score < 0 or score > 10:
            score = 0.0
        doc.metadata["score"] = score
        scored.append((doc, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [doc for doc, _ in scored[:top_k]]


def merge(vector_docs, bm25_docs):
    seen, merged = set(), []
    for doc in vector_docs + bm25_docs:
        if doc.page_content not in seen:
            seen.add(doc.page_content)
            merged.append(doc)
    return merged


def keys(docs):
    return [d.page_content[:60] for d in docs[:TOP_K]]


def jaccard(a, b):
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if (sa | sb) else 1.0


def preview(text, n=46):
    return text.replace("\n", " ")[:n]


def main():
    svc = DocumentIndexService(builtin_knowledge_service=BuiltinKnowledgeService())
    db = svc._load_conversation_db(CONVERSATION_ID)
    if db is None:
        print(f"无法加载会话索引：{CONVERSATION_ID}")
        return

    llm = build_llm()
    print(f"会话 {CONVERSATION_ID}　Top-K={TOP_K}　每篇截断 {CHARS_PER_DOC} 字\n")

    stats = {"per_doc": [], "batch": [], "rrf": []}
    agree_batch = []

    for query in QUERIES:
        vector_docs = svc._search_conversation_docs(db, query, limit=TOP_K * 2)
        bm25_docs = svc._bm25_retrieve(CONVERSATION_ID, query, k=TOP_K * 2)
        merged = merge(vector_docs, bm25_docs)
        if len(merged) <= TOP_K:
            print(f"Q: {query} —— 合并 {len(merged)} 篇，不触发重排，跳过\n")
            continue

        print("=" * 78)
        print(f"Q: {query}　（向量 {len(vector_docs)} + BM25 {len(bm25_docs)} → 合并 {len(merged)}）")

        t0 = time.time()
        per_doc = svc._rerank_with_llm(query, copy.deepcopy(merged), TOP_K)
        t_per = time.time() - t0

        t0 = time.time()
        try:
            batch = batch_score(llm, query, copy.deepcopy(merged), TOP_K)
            t_batch = time.time() - t0
            batch_ok = True
        except Exception as exc:
            print(f"  批量打分失败：{exc}")
            batch, t_batch, batch_ok = [], time.time() - t0, False

        t0 = time.time()
        rrf = rrf_fuse([vector_docs, bm25_docs], top_k=TOP_K)
        t_rrf = time.time() - t0

        stats["per_doc"].append(t_per)
        stats["batch"].append(t_batch)
        stats["rrf"].append(t_rrf)

        print(f"  逐篇 LLM  {t_per*1000:8.1f} ms  ({len(merged)} 次往返)")
        for i, d in enumerate(per_doc):
            print(f"      {i+1}. [{d.metadata.get('score')}] {preview(d.page_content)}")
        if batch_ok:
            print(f"  批量 LLM  {t_batch*1000:8.1f} ms  (1 次往返)")
            for i, d in enumerate(batch):
                print(f"      {i+1}. [{d.metadata.get('score')}] {preview(d.page_content)}")
            j = jaccard(keys(per_doc), keys(batch))
            agree_batch.append(j)
            print(f"      → 与逐篇打分的 Top-{TOP_K} 重合 Jaccard = {j:.2f}")
        print(f"  RRF        {t_rrf*1000:8.1f} ms  (0 次往返)")
        for i, d in enumerate(rrf):
            print(f"      {i+1}. [{d.metadata.get('score'):.4f}] {preview(d.page_content)}")
        j_rrf = jaccard(keys(per_doc), keys(rrf))
        print(f"      → 与逐篇打分的 Top-{TOP_K} 重合 Jaccard = {j_rrf:.2f}")
        print()

    print("=" * 78)
    n = len(stats["per_doc"])
    if n:
        sp = sum(stats["per_doc"]) * 1000
        sb = sum(stats["batch"]) * 1000
        sr = sum(stats["rrf"]) * 1000
        print(f"总耗时　逐篇 {sp:.0f} ms　批量 {sb:.0f} ms　RRF {sr:.0f} ms")
        print(f"相对逐篇：批量 {sp/sb:.1f}x 提速　RRF {sp/max(sr,0.001):.0f}x 提速")
        if agree_batch:
            print(f"批量 vs 逐篇　平均 Top-{TOP_K} 重合 {sum(agree_batch)/len(agree_batch):.2f}")


if __name__ == "__main__":
    main()
