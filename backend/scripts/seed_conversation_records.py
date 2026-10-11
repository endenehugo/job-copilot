"""集成脚本：调用真实后端 API 产生可持久化的对话记录，并验证增量摘要落库。

用法（在 backend/ 目录下，需后端已在 8018 运行）：
    py -3.12 scripts/seed_conversation_records.py
    py -3.12 scripts/seed_conversation_records.py --turns 26 --base-url http://127.0.0.1:8018

行为：
1. POST /conversation/create 建会话；
2. 连续多轮 POST /conversation/chat（mode=memory，纯文本、跳过文档检索与校验以省 token），
   超过 20 条消息后触发 context_compression_service 的增量摘要；
3. 结束时直接读 MySQL conversations 表，打印 context_summary 与 summarized_until_id，
   证明摘要已持久化；会话与消息可在前端对话列表看到。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings


# 模拟一段求职准备对话，逐轮发送；>20 条消息后触发增量摘要
_TURN_POOL = [
    "我想应聘后端开发岗位，先帮我梳理下技术栈重点。",
    "我在一个求职助手项目里用了 FastAPI 和 LangChain。",
    "这个项目还接入了 FAISS 做文档向量检索。",
    "简历里的项目描述怎么写更突出个人贡献？",
    "帮我量化一下：接口平均响应从 800ms 降到 120ms。",
    "面试时被问 RAG 的检索链路怎么设计？",
    "我打算用 BM25 加向量召回再做重排。",
    "会话超过一定轮数要做上下文压缩，你怎么看？",
    "我现在想加一个增量摘要，避免每轮从头重算。",
    "增量摘要用旧摘要加新溢出的消息合并成新摘要。",
    "水位线用消息自增 id 记录已经摘要到哪里。",
    "数据库这块我用的 MySQL 加 SQLAlchemy。",
    "会话表加两个字段存摘要和水位线。",
    "乐观锁防止并发时摘要水位回退。",
    "简历版本管理我想记录每次评分趋势。",
    "JD 解析和简历打分别接口怎么拆？",
    "面试模拟需要按轮次出题并评估回答。",
    "导出功能我想支持 Markdown。",
    "测试我用 pytest 加 SQLite 内存库覆盖接口。",
    "真实链路再写一个落库脚本验证。",
    "前端用 Vue3 加 Vite，代理到后端。",
    "部署用 systemd 跑 uvicorn，nginx 反代。",
    "帮我把这一串整理成一段 STAR 结构的项目故事。",
    "开头用一句话讲清楚这个项目解决了什么问题。",
    "过程强调我独立设计增量摘要和乐观锁。",
    "结果用接口响应和 token 占用下降来量化。",
]


def _create_conversation(base_url: str) -> str:
    resp = requests.post(
        f"{base_url}/api/v1/conversation/create",
        json={"title": "增量摘要落库验证", "mode": "memory"},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != "success":
        raise RuntimeError(f"创建会话失败：{body}")
    return body["data"]["conversation_id"]


def _chat(base_url: str, conversation_id: str, query: str) -> tuple[bool, str]:
    resp = requests.post(
        f"{base_url}/api/v1/conversation/chat",
        json={"conversation_id": conversation_id, "query": query, "mode": "memory"},
        timeout=120,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != "success":
        return False, str(body)
    answer = body["data"].get("answer", "")
    return True, answer[:60].replace("\n", " ")


def _read_summary(conversation_id: str) -> tuple[str, int, int]:
    """直连 MySQL 读取该会话的摘要、水位线与消息条数。"""
    import pymysql

    conn = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
        charset=settings.mysql_charset,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT context_summary, summarized_until_id FROM conversations WHERE conversation_id=%s",
                (conversation_id,),
            )
            row = cur.fetchone()
            summary, watermark = (row[0] or "", row[1] or 0) if row else ("", 0)
            cur.execute(
                "SELECT COUNT(*) FROM conversation_messages WHERE conversation_id=%s",
                (conversation_id,),
            )
            msg_count = cur.fetchone()[0]
        return summary, watermark, msg_count
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="调用真实后端产生对话记录并验证增量摘要落库")
    parser.add_argument("--base-url", default="http://127.0.0.1:8018")
    parser.add_argument("--turns", type=int, default=26, help="对话轮数（>11 轮即会触发摘要）")
    args = parser.parse_args()

    print(f"连接后端：{args.base_url}")
    health = requests.get(f"{args.base_url}/api/v1/system/health", timeout=10).json()
    print(f"健康检查：{health['code']} env={health['data']['env']}")

    cid = _create_conversation(args.base_url)
    print(f"会话已创建：{cid}\n")

    total_turns = min(args.turns, len(_TURN_POOL))
    for i in range(total_turns):
        query = _TURN_POOL[i]
        started = time.time()
        try:
            ok_, preview = _chat(args.base_url, cid, query)
        except Exception as exc:
            print(f"[{i + 1:>2}/{total_turns}] 请求异常：{exc}")
            continue
        flag = "OK " if ok_ else "ERR"
        print(f"[{i + 1:>2}/{total_turns}] {flag} {query[:24]}... → {preview}  ({time.time() - started:.1f}s)")

    print("\n=== 落库校验（直连 MySQL）===")
    summary, watermark, msg_count = _read_summary(cid)
    print(f"消息条数          : {msg_count}")
    print(f"summarized_until_id: {watermark}")
    print(f"context_summary    : {(summary[:200] + '...') if len(summary) > 200 else summary} (共 {len(summary)} 字)")

    if watermark > 0 and summary:
        print("\n增量摘要已持久化 ✅ 前端对话列表可查看该会话。")
    else:
        print("\n尚未触发摘要压缩（轮数不足或消息未超阈值）。可增加 --turns 重试。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
