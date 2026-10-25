"""读取 Locust *_stats.csv，生成阶段与接口级 Markdown 对比报告。"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

MISSING = "N/A"
PERCENTILES = ("50%", "90%", "95%", "99%")


@dataclass(frozen=True)
class StatsRow:
    kind: str
    name: str
    requests: int
    failures: int
    average_ms: float | None
    percentiles: dict[str, float | None]
    rps: float | None

    @property
    def fail_rate(self) -> float | None:
        if self.requests <= 0:
            return None
        return self.failures / self.requests


@dataclass(frozen=True)
class Report:
    label: str
    path: Path
    total: StatsRow
    endpoints: tuple[StatsRow, ...]


def _number(row: dict[str, str], key: str) -> float | None:
    raw = (row.get(key) or "").strip()
    if not raw or raw.upper() in {"N/A", "NA"}:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _int_number(row: dict[str, str], key: str) -> int:
    value = _number(row, key)
    return int(value) if value is not None else 0


def _make_row(row: dict[str, str]) -> StatsRow:
    percentiles = {key: _number(row, key) for key in PERCENTILES}
    if percentiles["50%"] is None:
        percentiles["50%"] = _number(row, "Median Response Time")
    return StatsRow(
        kind=(row.get("Type") or "").strip(),
        name=(row.get("Name") or "").strip(),
        requests=_int_number(row, "Request Count"),
        failures=_int_number(row, "Failure Count"),
        average_ms=_number(row, "Average Response Time"),
        percentiles=percentiles,
        rps=_number(row, "Requests/s"),
    )


def _weighted_average(rows: list[StatsRow]) -> float | None:
    available = [row for row in rows if row.average_ms is not None and row.requests > 0]
    count = sum(row.requests for row in available)
    if not count:
        return None
    return sum(row.average_ms * row.requests for row in available) / count


def _sum_optional(values: list[float | None]) -> float | None:
    if not values or any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def _stats_path(source: Path) -> Path:
    if source.is_file():
        if source.name.endswith("_stats_history.csv"):
            raise ValueError(f"{source} 是逐秒历史文件；请传 *_stats.csv 汇总文件")
        if not source.name.endswith("_stats.csv"):
            raise ValueError(f"{source} 不是 Locust *_stats.csv 文件")
        return source
    if not source.is_dir():
        raise FileNotFoundError(f"找不到输入路径：{source}")
    matches = sorted(source.glob("*_stats.csv"))
    if len(matches) != 1:
        raise ValueError(f"{source} 应恰好包含一个 *_stats.csv，实际找到 {len(matches)} 个")
    return matches[0]


def load_report(source: Path) -> Report:
    path = _stats_path(source)
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = [_make_row(row) for row in csv.DictReader(stream)]
    if not rows:
        raise ValueError(f"{path} 没有统计行")

    aggregate = next(
        (
            row
            for row in rows
            if row.name.lower() == "aggregated" or row.kind.lower() == "aggregated"
        ),
        None,
    )
    endpoints = tuple(
        row
        for row in rows
        if row.name
        and row.name.lower() != "aggregated"
        and row.requests > 0
    )
    if aggregate is None:
        # 只汇总可由各行精确相加/加权得到的指标；分位数无法从各接口分位数反推。
        aggregate = StatsRow(
            kind="",
            name="Aggregated",
            requests=sum(row.requests for row in endpoints),
            failures=sum(row.failures for row in endpoints),
            average_ms=_weighted_average(list(endpoints)),
            percentiles={key: None for key in PERCENTILES},
            rps=_sum_optional([row.rps for row in endpoints]),
        )
    suffix = "_stats.csv"
    label = source.name if source.is_dir() else path.name[: -len(suffix)]
    return Report(label, path, aggregate, endpoints)


def _fmt_count(value: int) -> str:
    return f"{value:,}"


def _fmt_ms(value: float | None) -> str:
    return f"{value:.1f} ms" if value is not None else MISSING


def _fmt_rate(value: float | None) -> str:
    return f"{value * 100:.2f}%" if value is not None else MISSING


def _fmt_rps(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else MISSING


def _fmt_delta(current: float | None, previous: float | None) -> str:
    if current is None or previous is None or previous == 0:
        return MISSING
    return f"{(current - previous) / previous * 100:+.1f}%"


def build_markdown(reports: list[Report]) -> str:
    lines = [
        "# Locust 阶段对比",
        "",
        "> 汇总指标来自各阶段的 Locust `_stats.csv`。P50/P90/P95/P99 只读取 Locust 导出的对应字段；不可用时显示 N/A。平均响应时间不能替代分位数。",
        "",
        "## 阶段总览",
        "",
        "| 阶段 | 请求数 | 成功数 | 失败数 | 失败率 | Avg | P50 | P90 | P95 | P99 | RPS |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for report in reports:
        row = report.total
        success = max(0, row.requests - row.failures)
        lines.append(
            f"| {report.label} | {_fmt_count(row.requests)} | {_fmt_count(success)} "
            f"| {_fmt_count(row.failures)} | {_fmt_rate(row.fail_rate)} "
            f"| {_fmt_ms(row.average_ms)} "
            + " ".join(f"| {_fmt_ms(row.percentiles[key])} " for key in PERCENTILES)
            + f"| {_fmt_rps(row.rps)} |"
        )

    lines.extend(
        [
            "",
            "## 相邻阶段变化",
            "",
            "| 对比 | Avg 变化 | P95 变化 | RPS 变化 |",
            "|---|---:|---:|---:|",
        ]
    )
    for previous, current in zip(reports, reports[1:]):
        lines.append(
            f"| {previous.label} → {current.label} "
            f"| {_fmt_delta(current.total.average_ms, previous.total.average_ms)} "
            f"| {_fmt_delta(current.total.percentiles['95%'], previous.total.percentiles['95%'])} "
            f"| {_fmt_delta(current.total.rps, previous.total.rps)} |"
        )
    if len(reports) < 2:
        lines.append("| 需要至少两个阶段 | N/A | N/A | N/A |")

    lines.extend(
        [
            "",
            "## 接口明细",
            "",
            "| 阶段 | 接口/请求名 | 请求数 | 失败数 | 失败率 | Avg | P50 | P90 | P95 | P99 | RPS |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for report in reports:
        for row in report.endpoints:
            name = row.name.replace("|", "\\|")
            lines.append(
                f"| {report.label} | {name} | {_fmt_count(row.requests)} "
                f"| {_fmt_count(row.failures)} | {_fmt_rate(row.fail_rate)} "
                f"| {_fmt_ms(row.average_ms)} "
                + " ".join(f"| {_fmt_ms(row.percentiles[key])} " for key in PERCENTILES)
                + f"| {_fmt_rps(row.rps)} |"
            )

    low_sample_rows = [
        f"{report.label} total (n={report.total.requests})"
        for report in reports
        if 0 < report.total.requests < 100
    ]
    low_sample_rows.extend(
        f"{report.label} {row.name} (n={row.requests})"
        for report in reports
        for row in report.endpoints
        if 0 < row.requests < 100
    )

    lines.extend(
        [
            "",
            "## 口径与限制",
            "",
            "- 阶段总览按 Locust `Aggregated` 行报告，仅包含统计 CSV 中的请求；初始化请求以 `[setup]` 单列。Locust 2.47 的 CSV 快照可能早于 `on_stop` 清理请求，因此 `[cleanup]` 可能只出现在进程结束时的控制台汇总中，需一并核对。",
            "- 若输入文件没有聚合行，脚本只对计数、失败率、加权平均值和可加总 RPS 做可计算的汇总；总体分位数显示 N/A，因为不能由各接口分位数合成。",
            "- RPS 直接读取 CSV。请求数为 0 或字段缺失时不推算、不用 0 伪装。",
            "- 阶段标签默认取输入目录名；请使用包含并发数/场景的目录名以便留档。",
        ]
    )
    if low_sample_rows:
        lines.append(
            "- 小样本提示（少于 100 个请求）：Locust 的分位值仍按原样展示，但 P95/P99 稳定性不足，谨慎解读：" +
            "；".join(low_sample_rows)
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="对比 Locust *_stats.csv 汇总文件并生成 Markdown"
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="每个阶段的报告目录或 *_stats.csv 文件，按比较顺序传入",
    )
    parser.add_argument("--output", type=Path, default=None, help="Markdown 输出路径；省略则打印到 stdout")
    args = parser.parse_args()

    try:
        reports = [load_report(path) for path in args.inputs]
        markdown = build_markdown(reports)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(markdown, encoding="utf-8")
        print(f"已写入 {args.output}")
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
