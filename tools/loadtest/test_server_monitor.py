from __future__ import annotations

import csv
import json
import os
import shutil
import tempfile
import unittest

from analyze_server import load_rows, summarize, warnings, window
from server_monitor import (
    CSV_FIELDS,
    GROUPS,
    Sampler,
    classify_process,
    count_tcp_established,
    cpu_percent,
    main,
    parse_cpu_times,
    parse_loadavg,
    parse_meminfo,
    parse_net_dev,
    parse_proc_stat,
    parse_vmstat,
)

STAT_A = "cpu  100 10 50 800 20 5 5 10 0 0\ncpu0 1 1 1 1 1 1 1 1 0 0\n"
STAT_B = "cpu  160 10 90 880 40 5 15 10 0 0\n"
# user 差 60，sys 差 40+10=50（system+softirq），iowait 差 20，steal 差 0，idle 差 80；总差 210


class ProcParsingTests(unittest.TestCase):
    def test_cpu_percent_uses_deltas(self) -> None:
        pct = cpu_percent(parse_cpu_times(STAT_A), parse_cpu_times(STAT_B))
        self.assertAlmostEqual(pct["user_pct"], 60 / 210 * 100, places=2)
        self.assertAlmostEqual(pct["sys_pct"], 50 / 210 * 100, places=2)
        self.assertAlmostEqual(pct["iowait_pct"], 20 / 210 * 100, places=2)
        self.assertAlmostEqual(pct["idle_pct"], 80 / 210 * 100, places=2)
        self.assertEqual(pct["steal_pct"], 0.0)

    def test_cpu_percent_zero_delta(self) -> None:
        times = parse_cpu_times(STAT_A)
        self.assertEqual(cpu_percent(times, times)["idle_pct"], 0.0)

    def test_meminfo_vmstat_loadavg(self) -> None:
        mem = parse_meminfo("MemTotal:  2048000 kB\nMemAvailable:   512000 kB\nSwapTotal: 1024 kB\nSwapFree: 24 kB\n")
        self.assertEqual(
            (mem["MemTotal"], mem["MemAvailable"], mem["SwapTotal"], mem["SwapFree"]),
            (2048000, 512000, 1024, 24),
        )
        vm = parse_vmstat("nr_free_pages 1\npswpin 7\npswpout 9\n")
        self.assertEqual((vm["pswpin"], vm["pswpout"]), (7, 9))
        self.assertEqual(parse_loadavg("0.50 1.25 2.00 1/200 123\n"), (0.5, 1.25, 2.0))

    def test_net_dev_excludes_loopback(self) -> None:
        text = (
            "Inter-|   Receive  |  Transmit\n face |bytes packets|bytes packets\n"
            "    lo: 999 1 0 0 0 0 0 0 888 1 0 0 0 0 0 0\n"
            "  eth0: 1000 5 0 0 0 0 0 0 2000 6 0 0 0 0 0 0\n"
            "  eth1: 10 1 0 0 0 0 0 0 20 1 0 0 0 0 0 0\n"
        )
        self.assertEqual(parse_net_dev(text), {"rx_bytes": 1010, "tx_bytes": 2020})

    def test_tcp_established(self) -> None:
        text = "  sl  local_address rem_address   st\n   0: A:1 B:2 01 x\n   1: A:1 B:2 0A x\n   2: A:1 B:2 01 x\n"
        self.assertEqual(count_tcp_established(text), 2)

    def test_proc_stat_handles_spaces_and_parens_in_name(self) -> None:
        text = "42 (uvicorn (worker) x) S 1 2 3 4 5 6 7 8 9 10 300 200 0 0 20 0 1 0 100 5000 777 18446744073709551615"
        self.assertEqual(parse_proc_stat(text), ("uvicorn (worker) x", "S", 500, 777))

    def test_classify_process_order(self) -> None:
        self.assertEqual(classify_process("python", "/opt/x/uvicorn app.main:app --port 8000"), "backend")
        self.assertEqual(classify_process("python", "python -m locust -f a.py"), "locust")
        self.assertEqual(classify_process("mysqld", "/usr/sbin/mysqld"), "mysqld")
        self.assertEqual(classify_process("mariadbd", "mariadbd"), "mysqld")
        self.assertEqual(classify_process("nginx", "nginx: worker process"), "nginx")
        self.assertIsNone(classify_process("bash", "bash"))
        self.assertEqual(classify_process("python", "uvicorn app.main:app locust"), "backend")


def make_fake_proc(root, stat, avail=500000, pswpout=0):
    os.makedirs(os.path.join(root, "net"), exist_ok=True)
    os.makedirs(os.path.join(root, "self"), exist_ok=True)
    with open(os.path.join(root, "stat"), "w") as handle:
        handle.write(stat)
    with open(os.path.join(root, "meminfo"), "w") as handle:
        handle.write(f"MemTotal: 2048000 kB\nMemAvailable: {avail} kB\nSwapTotal: 1000 kB\nSwapFree: 400 kB\n")
    with open(os.path.join(root, "vmstat"), "w") as handle:
        handle.write(f"pswpin 0\npswpout {pswpout}\n")
    with open(os.path.join(root, "loadavg"), "w") as handle:
        handle.write("1.00 0.50 0.25 1/100 1\n")
    with open(os.path.join(root, "net", "dev"), "w") as handle:
        handle.write("h\nh\n eth0: 1000 1 0 0 0 0 0 0 2000 1 0 0 0 0 0 0\n")
    with open(os.path.join(root, "net", "tcp"), "w") as handle:
        handle.write("h\n 0: A B 01 x\n")
    with open(os.path.join(root, "net", "tcp6"), "w") as handle:
        handle.write("h\n")


def write_proc(root, pid, name, cmdline, ticks_u, ticks_s, rss):
    directory = os.path.join(root, str(pid))
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "stat"), "w") as handle:
        handle.write(f"{pid} ({name}) S 1 2 3 4 5 6 7 8 9 10 {ticks_u} {ticks_s} 0 0 20 0 1 0 1 5000 {rss} 0")
    with open(os.path.join(directory, "cmdline"), "wb") as handle:
        handle.write(cmdline.replace(" ", "\0").encode())


class SamplerTests(unittest.TestCase):
    def test_two_samples_compute_rates_and_groups(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            make_fake_proc(root, "cpu  0 0 0 100 0 0 0 0\n")
            write_proc(root, 10, "python", "uvicorn app.main:app", 0, 0, 1000)
            sampler = Sampler(proc_root=root, clk_tck=100, page_size=4096)
            first = sampler.sample(100.0)
            self.assertEqual(first["backend_procs"], 1)
            self.assertIsNone(first["cpu_user_pct"])
            make_fake_proc(root, "cpu  50 0 0 150 0 0 0 0\n", pswpout=100)  # user 差 50、idle 差 50 → user 50%
            write_proc(root, 10, "python", "uvicorn app.main:app", 100, 100, 2000)  # +200 ticks / 100 / 2s = 100%
            row = sampler.sample(102.0)
            self.assertAlmostEqual(row["backend_cpu_pct"], 100.0, places=1)
            self.assertAlmostEqual(row["backend_rss_mb"], 2000 * 4096 / 1048576, places=1)
            self.assertAlmostEqual(row["cpu_user_pct"], 50.0, places=1)
            self.assertAlmostEqual(row["swapout_ps"], 50.0, places=1)
            self.assertAlmostEqual(row["swap_used_mb"], 600 / 1024, places=2)
            self.assertEqual(row["tcp_established"], 1)
            self.assertEqual(row["locust_procs"], 0)

    def test_vanished_process_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            make_fake_proc(root, "cpu  0 0 0 100 0 0 0 0\n")
            write_proc(root, 10, "python", "locust -f a.py", 0, 0, 10)
            sampler = Sampler(proc_root=root)
            sampler.sample(1.0)
            shutil.rmtree(os.path.join(root, "10"))
            self.assertEqual(sampler.sample(2.0)["locust_procs"], 0)

    def test_mysql_query_failure_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            make_fake_proc(root, "cpu  0 0 0 100 0 0 0 0\n")

            def boom():
                raise RuntimeError("x")

            self.assertIsNone(Sampler(proc_root=root, mysql_query=boom).sample(1.0)["mysql_threads_connected"])

    def test_sampler_excludes_its_own_process(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            make_fake_proc(root, "cpu  0 0 0 100 0 0 0 0\n")
            # 监控自己的命令行含 --wrap "locust …" 时不能把自己算进 locust 组
            write_proc(root, os.getpid(), "python3", "server_monitor.py --wrap locust -f a.py", 0, 0, 10)
            self.assertEqual(Sampler(proc_root=root).sample(1.0)["locust_procs"], 0)

    def test_csv_fields_cover_every_group(self) -> None:
        for group in GROUPS:
            for suffix in ("cpu_pct", "rss_mb", "procs"):
                self.assertIn(f"{group}_{suffix}", CSV_FIELDS)


class AnalyzeServerTests(unittest.TestCase):
    def _rows(self, count, busy, avail, swapout=0.0, steal=0.0, iowait=0.0):
        rows = []
        for index in range(count):
            row = {
                "ts": 1000.0 + index, "cpu_idle_pct": 100.0 - busy, "cpu_iowait_pct": iowait,
                "cpu_steal_pct": steal, "load1": 1.0, "mem_total_mb": 2000.0, "mem_available_mb": avail,
                "swap_used_mb": 0.0, "swapin_ps": 0.0, "swapout_ps": swapout,
                "mysql_threads_connected": None, "mysql_slow_queries": None,
            }
            for group in GROUPS:
                row.update({f"{group}_cpu_pct": 10.0, f"{group}_rss_mb": 100.0, f"{group}_procs": 1})
            rows.append(row)
        return rows

    def test_unhealthy_rows_raise_swap_memory_and_cpu_warnings(self) -> None:
        joined = "\n".join(warnings(summarize(self._rows(12, busy=90, avail=150, swapout=3.0))))
        self.assertIn("swap", joined)
        self.assertIn("可用内存", joined)
        self.assertIn("CPU", joined)
        self.assertNotIn("steal", joined)

    def test_iowait_and_steal_warnings(self) -> None:
        joined = "\n".join(warnings(summarize(self._rows(12, busy=20, avail=1500, steal=2.0, iowait=15.0))))
        self.assertIn("steal", joined)
        self.assertIn("iowait", joined)

    def test_healthy_rows_have_no_warnings(self) -> None:
        self.assertEqual(warnings(summarize(self._rows(12, busy=20, avail=1500))), [])

    def test_short_cpu_spike_is_not_flagged(self) -> None:
        rows = self._rows(12, busy=20, avail=1500)
        for row in rows[3:8]:  # 只连续 5 秒超过 80%，不到 10 秒
            row["cpu_idle_pct"] = 5.0
        self.assertEqual([item for item in warnings(summarize(rows)) if "CPU" in item], [])

    def test_summary_groups_and_system(self) -> None:
        summary = summarize(self._rows(12, busy=40, avail=1500))
        self.assertEqual(summary["samples"], 12)
        self.assertAlmostEqual(summary["system"]["cpu_busy"]["mean"], 40.0)
        self.assertEqual(summary["groups"]["backend"]["rss_max_mb"], 100.0)
        self.assertEqual(summary["system"]["mem_available_min_mb"], 1500.0)

    def test_mysql_values_are_summarized_when_present(self) -> None:
        rows = self._rows(3, busy=20, avail=1500)
        for index, row in enumerate(rows):
            row["mysql_threads_connected"] = 3.0 + index
            row["mysql_slow_queries"] = 10.0 + index
        summary = summarize(rows)
        self.assertEqual(summary["mysql"], {"threads_max": 5.0, "slow_delta": 2.0})

    def test_window_is_inclusive(self) -> None:
        self.assertEqual(len(window(self._rows(10, 20, 1500), 1003.0, 1005.0)), 3)
        self.assertEqual(len(window(self._rows(10, 20, 1500), None, None)), 10)

    def test_load_rows_blank_to_none(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "s.csv")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("ts,cpu_idle_pct\n1,\n2,50\n")
            rows = load_rows(path)
        self.assertIsNone(rows[0]["cpu_idle_pct"])
        self.assertEqual(rows[1]["cpu_idle_pct"], 50.0)


class MainTests(unittest.TestCase):
    def test_refuses_without_proc(self) -> None:
        with tempfile.TemporaryDirectory() as out, tempfile.TemporaryDirectory() as fake:
            self.assertEqual(main(["--out", out, "--label", "r1", "--proc-root", fake, "--duration", "1"]), 2)
            self.assertEqual(os.listdir(out), [])

    def test_duration_run_writes_csv_and_meta_and_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as out, tempfile.TemporaryDirectory() as root:
            make_fake_proc(root, "cpu  0 0 0 100 0 0 0 0\n")
            args = ["--out", out, "--label", "r1", "--proc-root", root, "--duration", "2", "--interval", "0.5"]
            self.assertEqual(main(args), 0)
            with open(os.path.join(out, "server-r1_server.csv"), encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertGreaterEqual(len(rows), 3)
            self.assertEqual(list(rows[0].keys()), CSV_FIELDS)
            with open(os.path.join(out, "server-r1_server_meta.json"), encoding="utf-8") as handle:
                meta = json.load(handle)
            self.assertIn("hostname", meta)
            self.assertEqual(meta["interval_s"], 0.5)
            self.assertEqual(main(args), 2)

    def test_wrap_runs_command_and_returns_its_exit_code(self) -> None:
        import sys

        with tempfile.TemporaryDirectory() as out, tempfile.TemporaryDirectory() as root:
            make_fake_proc(root, "cpu  0 0 0 100 0 0 0 0\n")
            command = f'"{sys.executable}" -c "import sys; sys.exit(3)"'
            code = main(["--out", out, "--label", "w1", "--proc-root", root, "--interval", "0.5", "--wrap", command])
            self.assertEqual(code, 3)
            self.assertTrue(os.path.exists(os.path.join(out, "server-w1_server.csv")))


if __name__ == "__main__":
    unittest.main()
