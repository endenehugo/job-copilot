"""验证 listwise（强制排序输出）能否改善批量打分的区分度退化问题。

批量打分时模型倾向给相近分数（如 8/8/8/8），排序退化为近似随机。
listwise 直接要求输出编号顺序，强制产生严格序。

用法：
    cd backend && D:/conda/python.exe scripts/bench_rerank_v3.py
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
CHARS = 300


def build_llm():
    return ChatTongyi(model="qwen-plus", temperature=0.1, top_p=0.5)


def batch_score(llm, query, docs, top_k=TOP_K):
    numbered = [f"[{i}] {d.page_content[:CHARS]}" for i, d in enumerate(docs)]
    prompt = (
        "你是文档相关性评估专家。下面是用户问题和若干候选文档，"
        "请判断每篇文档与用户问题的相关程度。\n\n"
        f"用户问题：{query}\n\n候选文档：\n" + "\n".join(numbered) + "\n\n"
        "请为每篇文档打 0-10 的整数分（0=完全不相关，10=高度相关）。"
        '只输出 JSON 数组，不要任何其他内容：\n[{"id":0,"score":8}, ...]'
    )
    raw = llm.invoke(prompt).content.strip()
    items = json.loads(re.search(r"\[.*\]", raw, re.DOTALL).group(0))
    sb = {}
    for it in items:
        try:
            sb[int(it["id"])] = float(it["score"])
        except (KeyError, TypeError, ValueError):
            continue
    out = []
    for i, d in enumerate(docs):
        s = sb.get(i)
        d.metadata["score"] = s if (s is not None and 0 <= s <= 10) else 0.0
        out.append((d, d.metadata["score"]))
    out.sort(key=lambda x: x[1], reverse=True)
    return [d for d, _ in out[:top_k]]


def listwise_rank(llm, query, docs, top_k=TOP_K):
    numbered = [f"[{i}] {d.page_content[:CHARS]}" for i, d in enumerate(docs)]
    prompt = (
        "你是文档相关性排序专家。下面是用户问题和若干候选文档。\n\n"
        f"用户问题：{query}\n\n候选文档：\n" + "\n".join(numbered) + "\n\n"
        f"请按与用户问题的相关程度从高到低排序，输出最相关的 {top_k} 篇文档编号。"
        f'只输出 JSON 数组，不要任何其他内容：\n[3, 1, 7, 2]'
    )
    raw = llm.invoke(prompt).content.strip()
    ids = json.loads(re.search(r"\[.*\]", raw, re.DOTALL).group(0))
    seen, ordered = set(), []
    for x in ids:
        try:
            i = int(x)
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(docs) and i not in seen:
            seen.add(i)
            ordered.append(i)
    for i in range(len(docs)):
        if len(ordered) >= top_k:
            break
        if i not in seen:
            ordered.append(i)
    out = []
    for rank, i in enumerate(ordered[:top_k]):
        d = docs[i]
        d.metadata["score"] = float(top_k - rank)
        out.append(d)
    return out


def merge(v, b):
    seen, m = set(), []
    for d in v + b:
        if d.page_content not in seen:
            seen.add(d.page_content)
            m.append(d)
    return m


def keys(ds):
    return [d.page_content[:60] for d in ds[:TOP_K]]


def jaccard(a, b):
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if (sa | sb) else 1.0


def preview(t, n=44):
    return t.replace("\n", " ")[:n]


def main():
    svc = DocumentIndexService(builtin_knowledge_service=BuiltinKnowledgeService())
    db = svc._load_conversation_db(CONVERSATION_ID)
    if db is None:
        print(f"无法加载 {CONVERSATION_ID}")
        return
    llm = build_llm()

    t_per = t_batch = t_list = 0.0
    j_batch, j_list = [], []

    for query in QUERIES:
        v = svc._search_conversation_docs(db, query, limit=TOP_K * 2)
        b = svc._bm25_retrieve(CONVERSATION_ID, query, k=TOP_K * 2)
        merged = merge(v, b)
        if len(merged) <= TOP_K:
            print(f"Q: {query} —— 合并 {len(merged)} 篇，跳过\n")
            continue

        print("=" * 78)
        print(f"Q: {query}　（合并 {len(merged)} 篇）")

        t0 = time.time()
        per = svc._rerank_with_llm(query, copy.deepcopy(merged), TOP_K)
        t_per += time.time() - t0

        t0 = time.time()
        bat = batch_score(llm, query, copy.deepcopy(merged))
        t_batch += time.time() - t0

        t0 = time.time()
        lst = listwise_rank(llm, query, copy.deepcopy(merged))
        t_list += time.time() - t0

        print(f"  逐篇（{len(merged)} 次往返）")
        for i, d in enumerate(per):
            print(f"      {i+1}. [{d.metadata.get('score')}] {preview(d.page_content)}")
        print("  批量打分（1 次往返）")
        for i, d in enumerate(bat):
            print(f"      {i+1}. [{d.metadata.get('score')}] {preview(d.page_content)}")
        print("  批量排序 listwise（1 次往返）")
        for i, d in enumerate(lst):
            print(f"      {i+1}. [{d.metadata.get('score')}] {preview(d.page_content)}")

        jb = jaccard(keys(per), keys(bat))
        jl = jaccard(keys(per), keys(lst))
        j_batch.append(jb)
        j_list.append(jl)
        print(f"  → 与逐篇 Top-{TOP_K} 重合：批量打分 {jb:.2f}　listwise {jl:.2f}\n")

    print("=" * 78)
    print(f"总耗时　逐篇 {t_per*1000:.0f} ms　批量打分 {t_batch*1000:.0f} ms　listwise {t_list*1000:.0f} ms")
    print(f"提速　　批量打分 {t_per/t_batch:.1f}x　listwise {t_per/t_list:.1f}x")
    print(f"与逐篇平均 Top-{TOP_K} 重合　批量打分 {sum(j_batch)/len(j_batch):.2f}　listwise {sum(j_list)/len(j_list):.2f}")


if __name__ == "__main__":
    main()
