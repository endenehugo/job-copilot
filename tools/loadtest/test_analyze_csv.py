from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from analyze_csv import build_markdown, load_report


HEADER = (
    "Type,Name,Request Count,Failure Count,Median Response Time,"
    "Average Response Time,Requests/s,50%,90%,95%,99%\n"
)


class AnalyzeCsvTests(unittest.TestCase):
    def test_reads_aggregate_and_endpoint_percentiles(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "stage-05_stats.csv"
            path.write_text(
                HEADER
                + "GET,GET /health,10,1,20,25,2.0,20,30,40,50\n"
                + ",Aggregated,10,1,20,25,2.0,20,30,40,50\n",
                encoding="utf-8",
            )
            report = load_report(path)
            markdown = build_markdown([report])

        self.assertEqual(report.total.requests, 10)
        self.assertEqual(report.total.failures, 1)
        self.assertEqual(report.total.percentiles["95%"], 40)
        self.assertIn("10.00%", markdown)
        self.assertIn("40.0 ms", markdown)

    def test_does_not_invent_aggregate_percentiles(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "stage-10_stats.csv"
            path.write_text(
                HEADER + "GET,GET /health,10,0,20,25,2.0,20,30,40,50\n",
                encoding="utf-8",
            )
            report = load_report(path)

        self.assertEqual(report.total.average_ms, 25)
        self.assertIsNone(report.total.percentiles["95%"])
        self.assertIsNone(report.total.percentiles["99%"])

    def test_uses_locust_median_as_p50_when_50_percent_column_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "stage_stats.csv"
            path.write_text(
                "Type,Name,Request Count,Failure Count,Median Response Time,Average Response Time,Requests/s,90%,95%,99%\n"
                "GET,GET /health,10,0,23,25,2.0,30,40,50\n"
                ",Aggregated,10,0,23,25,2.0,30,40,50\n",
                encoding="utf-8",
            )
            report = load_report(path)

        self.assertEqual(report.total.percentiles["50%"], 23)

    def test_rejects_history_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "stage_stats_history.csv"
            path.write_text("timestamp\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_report(path)


if __name__ == "__main__":
    unittest.main()
