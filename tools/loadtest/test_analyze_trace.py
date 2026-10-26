"""trace_support 的解析/落盘与 analyze_trace 的统计口径。"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import analyze_trace
from trace_support import REQUEST_FIELDS, parse_server_timing, request_row, write_requests


class ServerTimingTests(unittest.TestCase):
    def test_parse_skips_broken_segments(self) -> None:
        timing = parse_server_timing('total;dur=10.5, db;dur=2.0, llm;dur=7.0;desc="n=3", bad, x;dur=abc')
        self.assertEqual(timing, {"total": 10.5, "db": 2.0, "llm": 7.0, "llm_n": 3.0})

    def test_parse_none_and_empty(self) -> None:
        self.assertEqual(parse_server_timing(None), {})
        self.assertEqual(parse_server_timing(""), {})

    def test_parse_substage_desc(self) -> None:
        timing = parse_server_timing('retrieve;dur=300.0, rerank;dur=120.0;desc="of:sub"')
        self.assertEqual(timing, {"retrieve": 300.0, "rerank": 120.0})


class RequestRowTests(unittest.TestCase):
    def test_blank_server_columns_without_timing(self) -> None:
        row = request_row(3, 1, "2026-10-26T10:00:00+08:00", 2500.0, None, {})
        self.assertEqual(list(row), REQUEST_FIELDS)
        self.assertEqual(row["ok"], 1)
        self.assertEqual(row["server_total_ms"], "")
        self.assertEqual(row["client_ms"], 2500.0)

    def test_maps_stages_and_llm_calls(self) -> None:
        row = request_row(1, 2, "t", 3000.0, "E_SLOW|x", {"total": 2800.0, "compress": 900.0, "llm": 2000.0, "llm_n": 3.0})
        self.assertEqual((row["ok"], row["st_compress"], row["st_llm"], row["llm_calls"]), (0, 900.0, 2000.0, 3))
        self.assertEqual(row["reason"], "E_SLOW|x")

    def test_write_requests_header_and_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_requests(str(Path(tmp) / "r1"), [request_row(1, 1, "t", 10.0, None, {})])
            self.assertEqual(path.name, "r1_ai_requests.csv")
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(lines[0].split(","), REQUEST_FIELDS)
            self.assertEqual(len(lines), 2)


def _row(seq: int, client: float, total, compress="", llm="", retrieve="", ok=1) -> dict:
    row = {field: "" for field in REQUEST_FIELDS}
    row.update(
        {
            "seq": seq,
            "user": 1,
            "client_ms": client,
            "ok": ok,
            "server_total_ms": total,
            "st_compress": compress,
            "st_llm": llm,
            "st_retrieve": retrieve,
        }
    )
    return row


class StageTableTests(unittest.TestCase):
    def test_percentiles_and_share(self) -> None:
        rows = [_row(1, 120.0, 100.0, compress=10.0), _row(2, 220.0, 200.0, compress=20.0), _row(3, 320.0, 300.0, compress=30.0)]
        table = {item["stage"]: item for item in analyze_trace.stage_table(rows)}
        self.assertEqual((table["total"]["p50"], round(table["total"]["p95"])), (200.0, 290))
        self.assertEqual(table["compress"]["mean"], 20.0)
        self.assertEqual(round(table["compress"]["share"]), 10)

    def test_rows_without_server_total_are_excluded(self) -> None:
        rows = [_row(1, 120.0, 100.0), _row(2, 900.0, "", ok=0)]
        table = analyze_trace.stage_table(rows)
        self.assertEqual(table[0]["n"], 1)

    def test_llm_and_rerank_are_marked_overlapping(self) -> None:
        rows = [_row(1, 120.0, 100.0, llm=80.0, retrieve=50.0)]
        table = {item["stage"]: item for item in analyze_trace.stage_table(rows)}
        self.assertTrue(table["llm"]["overlap"])
        self.assertFalse(table["retrieve"].get("overlap"))


class OrdinalTests(unittest.TestCase):
    def test_groups_by_request_ordinal_and_flags_focus_window(self) -> None:
        rows = [_row(seq, 5000.0 if seq >= 7 else 1000.0, 900.0) for seq in range(1, 12)]
        ordinal = analyze_trace.by_ordinal(rows)
        self.assertEqual([item["seq"] for item in ordinal], list(range(1, 12)))
        flagged = [item["seq"] for item in ordinal if item["in_focus"]]
        self.assertEqual(flagged, [7, 8, 9, 10])
        self.assertEqual(ordinal[0]["client_mean"], 1000.0)
        self.assertEqual(ordinal[8]["client_mean"], 5000.0)

    def test_failed_rows_still_count_in_ordinal(self) -> None:
        rows = [_row(1, 100.0, "", ok=0), _row(1, 200.0, 90.0)]
        ordinal = analyze_trace.by_ordinal(rows)
        self.assertEqual((ordinal[0]["n"], ordinal[0]["errors"]), (2, 1))


class OverheadTests(unittest.TestCase):
    def test_gap_between_client_and_server(self) -> None:
        gaps = analyze_trace.overhead([_row(1, 120.0, 100.0), _row(2, 240.0, 200.0)])
        self.assertEqual(gaps["n"], 2)
        self.assertEqual(gaps["mean"], 30.0)


class MainTests(unittest.TestCase):
    def _write(self, directory: Path, label: str, rows: list[dict], comparable: dict) -> Path:
        csv_path = write_requests(str(directory / label), rows)
        config = {"config_id": "x", "comparable": comparable}
        (directory / f"{label}_ai_config.json").write_text(json.dumps(config), encoding="utf-8")
        return csv_path

    def test_same_config_prints_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            comparable = {"stage": "2", "mode": "memory"}
            first = self._write(directory, "r1", [_row(1, 120.0, 100.0, compress=10.0)], comparable)
            second = self._write(directory, "r2", [_row(1, 130.0, 110.0, compress=12.0)], comparable)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(analyze_trace.main([str(first), str(second)]), 0)
            text = out.getvalue()
            self.assertIn("r1_ai_requests.csv", text)
            self.assertIn("r2_ai_requests.csv", text)

    def test_different_config_refuses_to_compare(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            first = self._write(directory, "r1", [_row(1, 120.0, 100.0)], {"stage": "2", "model": "a"})
            second = self._write(directory, "r2", [_row(1, 120.0, 100.0)], {"stage": "2", "model": "b"})
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(analyze_trace.main([str(first), str(second)]), 1)
            self.assertIn("model", err.getvalue())

    def test_missing_file_returns_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(analyze_trace.main([str(Path(tmp) / "nope_ai_requests.csv")]), 2)


if __name__ == "__main__":
    unittest.main()
