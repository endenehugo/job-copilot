"""把后端的 Server-Timing 响应头落成逐请求 CSV。

后端只在 `PERF_TRACE_HEADER=1` 且请求带 `X-Perf-Trace: 1` 时返回这个头，
所以这里是纯解析 + 组行，不做任何网络访问。头缺失或格式异常时服务端列留空，
客户端耗时列照常写：同一份文件既能看阶段拆分，也能单独算原始分位数。
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Mapping

# 顺序即 CSV 列顺序，改动要同步 analyze_trace.py 与测试
REQUEST_FIELDS = [
    "seq",
    "user",
    "started_at",
    "client_ms",
    "ok",
    "reason",
    "server_total_ms",
    "st_db",
    "st_init",
    "st_compress",
    "st_retrieve",
    "st_rerank",
    "st_answer",
    "st_agent",
    "st_tool",
    "st_verify",
    "st_llm",
    "llm_calls",
]

# CSV 列 → Server-Timing 里的指标名
_STAGE_METRICS = {
    "st_db": "db",
    "st_init": "init",
    "st_compress": "compress",
    "st_retrieve": "retrieve",
    "st_rerank": "rerank",
    "st_answer": "answer",
    "st_agent": "agent",
    "st_tool": "tool",
    "st_verify": "verify",
    "st_llm": "llm",
}

_N_PARTS = re.compile(r"n=(\d+)")


def parse_server_timing(header: str | None) -> dict[str, float]:
    """`total;dur=10.5, db;dur=2.0, llm;dur=7.0;desc="n=3"` → 指标名到毫秒。

    没有 dur 的段（如 `bad`）和 dur 不是数字的段直接跳过，不抛异常：
    响应头解析失败只应该让服务端列为空，不应该让压测判失败。
    """
    timing: dict[str, float] = {}
    if not header:
        return timing
    for part in header.split(","):
        fields = [item.strip() for item in part.split(";")]
        name = fields[0]
        if not name:
            continue
        duration = None
        calls = None
        for field in fields[1:]:
            if field.startswith("dur="):
                try:
                    duration = float(field[4:])
                except ValueError:
                    duration = None
            else:
                match = _N_PARTS.search(field)
                if match:
                    calls = float(match.group(1))
        if duration is None:
            continue
        timing[name] = duration
        if calls is not None:
            timing[f"{name}_n"] = calls
    return timing


def request_row(
    seq: int,
    user: int,
    started_at: str,
    client_ms: float,
    reason: str | None,
    timing: Mapping[str, float],
) -> dict:
    """一次 chat 请求对应一行。seq 是该用户的第几次请求（从 1 起）。"""
    row: dict = {
        "seq": seq,
        "user": user,
        "started_at": started_at,
        "client_ms": round(client_ms, 1),
        "ok": 1 if reason is None else 0,
        "reason": reason or "",
        "server_total_ms": timing.get("total", ""),
    }
    for column, metric in _STAGE_METRICS.items():
        row[column] = timing.get(metric, "")
    calls = timing.get("llm_n")
    row["llm_calls"] = int(calls) if calls is not None else ""
    return {field: row[field] for field in REQUEST_FIELDS}


def requests_path(prefix: str) -> Path:
    return Path(f"{prefix}_ai_requests.csv")


def write_requests(prefix: str, rows: list[dict]) -> Path:
    """写 `<前缀>_ai_requests.csv`；rows 为空也写表头，避免分析器拿到空目录时无从判断。"""
    path = requests_path(prefix)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REQUEST_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path
