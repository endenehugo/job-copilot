"""汇总 server_monitor.py 产出的 *_server.csv：各进程组的 CPU/内存统计和系统层面的预警。

    python analyze_server.py reports/20261026/server-r1_server.csv --config reports/20261026/ai-s2-r1_ai_config.json

给了 --config 就只统计该轮压测 run.started_at ~ run.ended_at 的窗口。预警只陈述事实和阈值，
不下“已经是瓶颈”的结论。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime
from typing import Optional

from server_monitor import GROUPS

MEM_LOW_PCT = 10.0
IOWAIT_P95_LIMIT = 10.0
CPU_BUSY_LIMIT = 80.0
CPU_BUSY_MIN_SECONDS = 10.0


def load_rows(path: str) -> list[dict]:
    """读取 CSV；数值列转 float，空串转 None。"""
    rows: list[dict] = []
    with open(path, newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            row: dict = {}
            for key, value in raw.items():
                if value is None or value == "":
                    row[key] = None
                else:
                    try:
                        row[key] = float(value)
                    except ValueError:
                        row[key] = value
            rows.append(row)
    return rows


def window(rows: list[dict], start: Optional[float], end: Optional[float]) -> list[dict]:
    """按 ts 闭区间裁剪；start/end 为 None 表示不限。"""
    return [
        row for row in rows
        if row.get("ts") is not None
        and (start is None or row["ts"] >= start)
        and (end is None or row["ts"] <= end)
    ]


def _values(rows: list[dict], key: str) -> list[float]:
    return [row[key] for row in rows if isinstance(row.get(key), (int, float))]


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def _stat(values: list[float]) -> Optional[dict]:
    if not values:
        return None
    return {"mean": sum(values) / len(values), "p95": _percentile(values, 0.95), "max": max(values)}


def _longest_run_seconds(rows: list[dict], predicate) -> float:
    """predicate 连续为真的最长时长（秒），按这一段首尾 ts 之差计；单个孤立的点为 0 秒。"""
    longest = 0.0
    run_start: Optional[float] = None
    for row in rows:
        if predicate(row):
            if run_start is None:
                run_start = row["ts"]
            longest = max(longest, row["ts"] - run_start)
        else:
            run_start = None
    return longest


def summarize(rows: list[dict], meta: Optional[dict] = None) -> dict:
    timestamps = _values(rows, "ts")
    busy = [100.0 - value for value in _values(rows, "cpu_idle_pct")]
    mem_total = max(_values(rows, "mem_total_mb") or [0.0])
    avail = _values(rows, "mem_available_mb")
    summary = {
        "samples": len(rows),
        "seconds": (max(timestamps) - min(timestamps)) if timestamps else 0.0,
        "system": {
            "cpu_busy": _stat(busy),
            "iowait_p95": _percentile(_values(rows, "cpu_iowait_pct"), 0.95) if _values(rows, "cpu_iowait_pct") else None,
            "steal_max": max(_values(rows, "cpu_steal_pct") or [0.0]),
            "load1_max": max(_values(rows, "load1") or [0.0]),
            "mem_total_mb": mem_total,
            "mem_available_min_mb": min(avail) if avail else None,
            "mem_available_min_pct": (min(avail) / mem_total * 100) if avail and mem_total else None,
            "swap_used_max_mb": max(_values(rows, "swap_used_mb") or [0.0]),
            "swap_activity": any(value > 0 for value in _values(rows, "swapin_ps") + _values(rows, "swapout_ps")),
        },
        "groups": {},
        "mysql": None,
    }
    for group in GROUPS:
        summary["groups"][group] = {
            "cpu": _stat(_values(rows, f"{group}_cpu_pct")),
            "rss_max_mb": max(_values(rows, f"{group}_rss_mb") or [0.0]),
            "procs_max": max(_values(rows, f"{group}_procs") or [0]),
        }
    threads, slow = _values(rows, "mysql_threads_connected"), _values(rows, "mysql_slow_queries")
    if threads and slow:
        summary["mysql"] = {"threads_max": max(threads), "slow_delta": slow[-1] - slow[0]}
    summary["_cpu_busy_run_s"] = _longest_run_seconds(
        rows, lambda row: isinstance(row.get("cpu_idle_pct"), (int, float)) and 100.0 - row["cpu_idle_pct"] > CPU_BUSY_LIMIT
    )
    if meta:
        summary["meta"] = meta
    return summary


def warnings(summary: dict) -> list[str]:
    """只返回达到阈值的事实，没有问题时返回空列表。"""
    system = summary["system"]
    found: list[str] = []
    if system["swap_activity"]:
        found.append(f"swap 有换入换出活动（已用 swap 峰值 {system['swap_used_max_mb']:.0f} MB）")
    pct = system["mem_available_min_pct"]
    if pct is not None and pct < MEM_LOW_PCT:
        found.append(
            f"可用内存最低 {system['mem_available_min_mb']:.0f} MB，占总量 {pct:.1f}%（阈值 {MEM_LOW_PCT:.0f}%）"
        )
    if system["iowait_p95"] is not None and system["iowait_p95"] > IOWAIT_P95_LIMIT:
        found.append(f"iowait P95 为 {system['iowait_p95']:.1f}%（阈值 {IOWAIT_P95_LIMIT:.0f}%）")
    if system["steal_max"] > 0:
        found.append(f"steal 非零，最大 {system['steal_max']:.1f}%（宿主机在抢占本机 CPU）")
    if summary.get("_cpu_busy_run_s", 0.0) > CPU_BUSY_MIN_SECONDS:
        found.append(
            f"系统 CPU 连续超过 {CPU_BUSY_LIMIT:.0f}% 达 {summary['_cpu_busy_run_s']:.0f} 秒（阈值 {CPU_BUSY_MIN_SECONDS:.0f} 秒）"
        )
    return found


def _fmt(stat: Optional[dict], suffix: str = "%") -> str:
    if not stat:
        return "N/A"
    return f"均值 {stat['mean']:.1f}{suffix} / P95 {stat['p95']:.1f}{suffix} / 最大 {stat['max']:.1f}{suffix}"


def format_report(summary: dict) -> str:
    system = summary["system"]
    lines = [
        f"采样 {summary['samples']} 行，窗口 {summary['seconds']:.0f} 秒",
        f"系统 CPU 占用：{_fmt(system['cpu_busy'])}；load1 最大 {system['load1_max']:.2f}",
    ]
    if system["mem_available_min_mb"] is not None:
        lines.append(
            f"可用内存最低 {system['mem_available_min_mb']:.0f} MB / 总量 {system['mem_total_mb']:.0f} MB"
            f"（{system['mem_available_min_pct']:.1f}%）；swap 已用峰值 {system['swap_used_max_mb']:.0f} MB"
        )
    lines.append("")
    lines.append("| 进程组 | CPU（100% = 满载一个核） | RSS 峰值 | 进程数峰值 |")
    lines.append("|---|---|---:|---:|")
    for group in GROUPS:
        data = summary["groups"][group]
        lines.append(f"| {group} | {_fmt(data['cpu'])} | {data['rss_max_mb']:.0f} MB | {data['procs_max']:.0f} |")
    if summary.get("mysql"):
        lines.append(
            f"\nMySQL：Threads_connected 峰值 {summary['mysql']['threads_max']:.0f}，窗口内 Slow_queries 增加 {summary['mysql']['slow_delta']:.0f}"
        )
    found = warnings(summary)
    lines.append("")
    lines.append("预警：" + ("无" if not found else ""))
    lines.extend(f"- {item}" for item in found)
    return "\n".join(lines)


def _config_window(path: str) -> tuple[Optional[float], Optional[float]]:
    with open(path, encoding="utf-8") as handle:
        run = json.load(handle).get("run", {})
    def parse(value):
        return datetime.fromisoformat(value).timestamp() if value else None
    return parse(run.get("started_at")), parse(run.get("ended_at"))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="汇总服务器资源采样")
    parser.add_argument("server_csv")
    parser.add_argument("--config", help="该轮压测的 *_ai_config.json，用于裁剪时间窗口")
    parser.add_argument("--meta", help="*_server_meta.json（默认按 CSV 同前缀查找）")
    args = parser.parse_args(argv)

    rows = load_rows(args.server_csv)
    if args.config:
        start, end = _config_window(args.config)
        rows = window(rows, start, end)
    if not rows:
        print("窗口内没有采样数据：检查监控与压测是否在同一台机器/同一时区，以及时间是否重叠", file=sys.stderr)
        return 1
    print(format_report(summarize(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
