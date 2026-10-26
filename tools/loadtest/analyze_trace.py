"""读 `_ai_requests.csv`，回答"慢在哪个阶段"和"第几次请求开始变慢"。

只做统计，不下结论。输入来自 `run_ai_stage.ps1 -Trace` 产出的逐请求 CSV，
以及同前缀的 `_ai_config.json`（用来确认几轮的可比性）。

分母约定：各阶段耗时与 total 是**同一量级但不成加法关系**——
llm 是跨阶段的合计，rerank 是 retrieve 的子集，所以占比一律以 total 的均值为分母，
不把各阶段相加当 100%。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Optional

from run_manifest import diff
from trace_support import REQUEST_FIELDS

STAGE_COLUMNS = [field for field in REQUEST_FIELDS if field.startswith("st_")]
# 这些阶段被别的阶段包住，单独看有意义，但不能和包含它的阶段相加
OVERLAP_STAGES = {"st_rerank", "st_llm"}


def load_requests(path: str | Path) -> list[dict]:
    """读 CSV，数字列转 float，空串保持空串（表示服务端没给这一列）。"""
    rows: list[dict] = []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            row: dict = {}
            for field in REQUEST_FIELDS:
                text = (raw.get(field) or "").strip()
                row[field] = text
            row["seq"] = _to_int(raw.get("seq"))
            row["user"] = _to_int(raw.get("user"))
            row["ok"] = _to_int(raw.get("ok"))
            for field in ("client_ms", "server_total_ms", *STAGE_COLUMNS, "llm_calls"):
                row[field] = _to_float(raw.get(field))
            rows.append(row)
    return rows


def _to_int(value) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _to_float(value) -> float | str:
    text = str(value).strip() if value is not None else ""
    if not text:
        return ""
    try:
        return float(text)
    except ValueError:
        return ""


def _percentile(values: list[float], fraction: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _mean(values: list[float]) -> Optional[float]:
    """空样本返回 None：会显示成 "-"，不能显示成 0.0（那会被读成"这个阶段不耗时"）。"""
    return sum(values) / len(values) if values else None


def _numbers(rows: Iterable[dict], key: str) -> list[float]:
    return [row[key] for row in rows if isinstance(row.get(key), float)]


def usable(rows: list[dict]) -> list[dict]:
    """能做阶段分析的行：成功且服务端给了 total。"""
    return [row for row in rows if row["ok"] == 1 and isinstance(row["server_total_ms"], float)]


def stage_table(rows: list[dict]) -> list[dict]:
    """每个阶段一行：n、p50、p95、mean、share（mean / total 的 mean）。"""
    sample = usable(rows)
    if not sample:
        return []
    total_mean = _mean(_numbers(sample, "server_total_ms"))
    table = [
        {
            "stage": "total",
            "n": len(sample),
            "p50": _percentile(_numbers(sample, "server_total_ms"), 0.5),
            "p95": _percentile(_numbers(sample, "server_total_ms"), 0.95),
            "mean": total_mean,
            "share": 100.0,
        }
    ]
    for column in STAGE_COLUMNS:
        values = _numbers(sample, column)
        if not values:
            continue
        table.append(
            {
                "stage": column[3:],
                "n": len(values),
                "p50": _percentile(values, 0.5),
                "p95": _percentile(values, 0.95),
                "mean": _mean(values),
                "share": (_mean(values) / total_mean * 100.0) if total_mean else 0.0,
                "overlap": column in OVERLAP_STAGES,
            }
        )
    return table


def by_ordinal(rows: list[dict], focus: tuple[int, int] = (7, 10)) -> list[dict]:
    """按"每个用户的第几次请求"分组。慢请求位置固定的现象靠这张表验证。"""
    groups: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        if row["seq"] is not None:
            groups[row["seq"]].append(row)
    result = []
    for seq in sorted(groups):
        group = groups[seq]
        result.append(
            {
                "seq": seq,
                "n": len(group),
                "client_p50": _percentile(_numbers(group, "client_ms"), 0.5),
                "client_mean": _mean(_numbers(group, "client_ms")),
                "server_mean": _mean(_numbers(group, "server_total_ms")),
                "compress_mean": _mean(_numbers(group, "st_compress")),
                "retrieve_mean": _mean(_numbers(group, "st_retrieve")),
                "llm_mean": _mean(_numbers(group, "st_llm")),
                "llm_calls_mean": _mean(_numbers(group, "llm_calls")),
                "errors": sum(1 for row in group if row["ok"] != 1),
                "in_focus": focus[0] <= seq <= focus[1],
            }
        )
    return result


def overhead(rows: list[dict]) -> dict:
    """client_ms - server_total_ms：网络 RTT、排队、SSE 收包之外的差距。"""
    pairs = [
        (row["client_ms"], row["server_total_ms"])
        for row in rows
        if isinstance(row["client_ms"], float) and isinstance(row["server_total_ms"], float)
    ]
    if not pairs:
        return {"n": 0}
    gaps = [client - server for client, server in pairs]
    return {
        "n": len(gaps),
        "p50": _percentile(gaps, 0.5),
        "p95": _percentile(gaps, 0.95),
        "mean": _mean(gaps),
    }


def check_same_config(manifests: list[tuple[str, dict]]) -> list[tuple]:
    """两两比较 comparable，返回 (轮次A, 轮次B, 字段, 值A, 值B)。空表示可一起汇总。"""
    differences: list[tuple] = []
    for index in range(len(manifests)):
        for other in range(index + 1, len(manifests)):
            label_a, data_a = manifests[index]
            label_b, data_b = manifests[other]
            for key, left, right in diff(data_a.get("comparable", {}), data_b.get("comparable", {})):
                differences.append((label_a, label_b, key, left, right))
    return differences


def _config_path(csv_path: Path) -> Path:
    name = csv_path.name
    if name.endswith("_ai_requests.csv"):
        return csv_path.with_name(name[: -len("_ai_requests.csv")] + "_ai_config.json")
    return csv_path.with_suffix(".json")


def _load_config(csv_path: Path) -> Optional[dict]:
    path = _config_path(csv_path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _fmt(value: Optional[float], digits: int = 1) -> str:
    if not isinstance(value, float):
        return "-"
    return f"{value:.{digits}f}"


def format_report(paths: list[Path], per_file: list[tuple[Path, list[dict]]]) -> str:
    lines: list[str] = []
    for path, rows in per_file:
        lines.append(f"## {path.name}（{len(rows)} 个请求，成功 {sum(1 for r in rows if r['ok'] == 1)}）")
        table = stage_table(rows)
        if not table:
            lines.append("  没有拿到 Server-Timing 的成功请求：确认服务端已设 PERF_TRACE_HEADER=1 且本轮带 -Trace。")
            continue
        lines.append("  阶段耗时（毫秒；share 以 total 均值为分母，rerank/llm 与包含它的阶段重叠）")
        lines.append("  | 阶段 | n | P50 | P95 | 均值 | share |")
        lines.append("  |---|---|---|---|---|---|")
        for item in table:
            mark = "（重叠）" if item.get("overlap") else ""
            lines.append(
                f"  | {item['stage']}{mark} | {item['n']} | {_fmt(item['p50'])} | "
                f"{_fmt(item['p95'])} | {_fmt(item['mean'])} | {_fmt(item['share'], 0)}% |"
            )
        gap = overhead(rows)
        if gap["n"]:
            lines.append(
                f"  客户端-服务端差值（网络+排队）ms：P50={_fmt(gap['p50'])} P95={_fmt(gap['p95'])} 均值={_fmt(gap['mean'])}"
            )
        ordinal = by_ordinal(rows)
        if ordinal:
            lines.append("  按每个用户的第 N 次请求（* 标记第 7-10 次）")
            lines.append("  | seq | n | client P50 | client 均值 | server 均值 | compress 均值 | retrieve 均值 | llm 均值 | llm 次数 | 失败 |")
            lines.append("  |---|---|---|---|---|---|---|---|---|---|")
            for item in ordinal:
                flag = "*" if item["in_focus"] else ""
                lines.append(
                    f"  | {item['seq']}{flag} | {item['n']} | {_fmt(item['client_p50'])} | {_fmt(item['client_mean'])} | "
                    f"{_fmt(item['server_mean'])} | {_fmt(item['compress_mean'])} | {_fmt(item['retrieve_mean'])} | "
                    f"{_fmt(item['llm_mean'])} | {_fmt(item['llm_calls_mean'])} | {item['errors']} |"
                )
        lines.append("")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="按 Server-Timing 汇总逐请求阶段耗时")
    parser.add_argument("csv", nargs="+", help="一轮或多轮的 *_ai_requests.csv")
    args = parser.parse_args(argv)

    paths = [Path(item) for item in args.csv]
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        print("找不到文件：" + ", ".join(missing), file=sys.stderr)
        return 2

    per_file = [(path, load_requests(path)) for path in paths]
    manifests = []
    for path in paths:
        config = _load_config(path)
        if config is not None:
            manifests.append((path.name, config))
    # 指纹不同的轮次不能合并统计：先把差异摆出来，让人决定要不要比
    differences = check_same_config(manifests) if len(manifests) > 1 else []
    if differences:
        print("这些轮次的配置不一致，不能直接对比：", file=sys.stderr)
        for label_a, label_b, key, left, right in differences:
            print(f"  {label_a} vs {label_b}: {key} = {left!r} → {right!r}", file=sys.stderr)
        return 1

    print(format_report(paths, per_file))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
