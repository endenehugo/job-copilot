"""服务器资源采样器：只用标准库读取 /proc，写出每秒一行的 CSV。

本文件前半部分是纯解析函数（便于用固定文本测试），后半部分是采样循环和命令行入口。
不需要 root，不依赖 sysstat。非 Linux 环境（没有 /proc）会拒绝运行。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import re
import shlex
import socket
import subprocess
import sys
import time
from datetime import datetime
from typing import Callable, Optional

_CPU_KEYS = ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal")


def parse_cpu_times(stat_text: str) -> dict[str, int]:
    """取 /proc/stat 第一行 "cpu ..."（所有核合计）的 jiffies。"""
    for line in stat_text.splitlines():
        parts = line.split()
        if parts and parts[0] == "cpu":
            values = [int(item) for item in parts[1:]]
            values += [0] * (len(_CPU_KEYS) - len(values))
            return dict(zip(_CPU_KEYS, values))
    raise ValueError("/proc/stat 中没有 cpu 汇总行")


def cpu_percent(prev: dict[str, int], cur: dict[str, int]) -> dict[str, float]:
    """两次采样之间各类 CPU 时间占比（%）；总差为 0 时全部为 0。"""
    delta = {key: max(0, cur[key] - prev[key]) for key in _CPU_KEYS}
    total = sum(delta.values())
    if total <= 0:
        return {"user_pct": 0.0, "sys_pct": 0.0, "iowait_pct": 0.0, "steal_pct": 0.0, "idle_pct": 0.0}
    scale = 100.0 / total
    return {
        "user_pct": (delta["user"] + delta["nice"]) * scale,
        "sys_pct": (delta["system"] + delta["irq"] + delta["softirq"]) * scale,
        "iowait_pct": delta["iowait"] * scale,
        "steal_pct": delta["steal"] * scale,
        "idle_pct": delta["idle"] * scale,
    }


def parse_meminfo(text: str) -> dict[str, int]:
    """/proc/meminfo 的 kB 值，键保持原名（MemTotal、MemAvailable、SwapTotal…）。"""
    result: dict[str, int] = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        fields = rest.split()
        if name and fields and fields[0].isdigit():
            result[name.strip()] = int(fields[0])
    return result


def parse_vmstat(text: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            result[parts[0]] = int(parts[1])
    return result


def parse_loadavg(text: str) -> tuple[float, float, float]:
    parts = text.split()
    return float(parts[0]), float(parts[1]), float(parts[2])


def parse_net_dev(text: str) -> dict[str, int]:
    """所有非回环网卡的累计收发字节。"""
    rx = tx = 0
    for line in text.splitlines():
        name, sep, rest = line.partition(":")
        if not sep or name.strip() == "lo":
            continue
        fields = rest.split()
        if len(fields) >= 9 and fields[0].isdigit() and fields[8].isdigit():
            rx += int(fields[0])
            tx += int(fields[8])
    return {"rx_bytes": rx, "tx_bytes": tx}


def count_tcp_established(text: str) -> int:
    """/proc/net/tcp(6) 中状态为 01（ESTABLISHED）的连接数。"""
    count = 0
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) > 3 and fields[3] == "01":
            count += 1
    return count


def parse_proc_stat(text: str) -> tuple[str, str, int, int]:
    """/proc/<pid>/stat → (进程名, 状态, utime+stime 的 ticks, rss 页数)。

    进程名放在括号里且可能含空格和括号，所以必须按最后一个 ")" 切分。
    """
    start = text.find("(")
    end = text.rfind(")")
    if start < 0 or end < start:
        raise ValueError("无法解析 /proc/<pid>/stat")
    fields = text[end + 1 :].split()
    return text[start + 1 : end], fields[0], int(fields[11]) + int(fields[12]), int(fields[21])


def classify_process(comm: str, cmdline: str) -> Optional[str]:
    """进程归组，只归入第一个匹配的组：backend > locust > mysqld > nginx。"""
    if "uvicorn" in cmdline and "app.main" in cmdline:
        return "backend"
    if "locust" in cmdline or comm == "locust":
        return "locust"
    if comm in ("mysqld", "mariadbd") or "mysqld" in cmdline or "mariadbd" in cmdline:
        return "mysqld"
    if comm == "nginx" or cmdline.startswith("nginx"):
        return "nginx"
    return None


GROUPS = ("backend", "locust", "mysqld", "nginx")
_RATE_FIELDS = (
    "cpu_user_pct", "cpu_sys_pct", "cpu_iowait_pct", "cpu_steal_pct", "cpu_idle_pct",
    "swapin_ps", "swapout_ps", "net_rx_bps", "net_tx_bps",
)
CSV_FIELDS = (
    ["ts", "lag_ms", "cpu_user_pct", "cpu_sys_pct", "cpu_iowait_pct", "cpu_steal_pct", "cpu_idle_pct"]
    + ["load1", "load5", "load15", "mem_total_mb", "mem_available_mb", "swap_used_mb", "swapin_ps", "swapout_ps"]
    + ["net_rx_bps", "net_tx_bps", "tcp_established"]
    + [f"{group}_{suffix}" for group in GROUPS for suffix in ("cpu_pct", "rss_mb", "procs")]
    + ["mysql_threads_connected", "mysql_slow_queries"]
)
MYSQL_INTERVAL_S = 5.0


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


class Sampler:
    """每次 sample() 读一遍 /proc 并返回一行；速率类字段是与上一次采样的差分，首次为 None。"""

    def __init__(
        self,
        proc_root: str = "/proc",
        clk_tck: int = 100,
        page_size: int = 4096,
        mysql_query: Optional[Callable[[], Optional[tuple[int, int]]]] = None,
    ) -> None:
        self.proc_root = proc_root
        self.clk_tck = clk_tck
        self.page_size = page_size
        self.mysql_query = mysql_query
        self._prev_time: Optional[float] = None
        self._prev_cpu: Optional[dict[str, int]] = None
        self._prev_vm: Optional[dict[str, int]] = None
        self._prev_net: Optional[dict[str, int]] = None
        self._prev_ticks: dict[int, int] = {}
        self._own_pid = os.getpid()
        self._mysql_at: Optional[float] = None
        self._mysql_value: Optional[tuple[int, int]] = None

    def _path(self, *parts: str) -> str:
        return os.path.join(self.proc_root, *parts)

    def _mysql(self, now: float) -> Optional[tuple[int, int]]:
        if self.mysql_query is None:
            return None
        if self._mysql_at is None or now - self._mysql_at >= MYSQL_INTERVAL_S:
            self._mysql_at = now
            try:
                self._mysql_value = self.mysql_query()
            except Exception:  # MySQL 不可用不能影响系统采样
                self._mysql_value = None
        return self._mysql_value

    def _processes(self, dt: Optional[float]) -> dict[str, dict[str, float]]:
        groups = {name: {"cpu": 0.0, "rss": 0.0, "procs": 0, "seen": False} for name in GROUPS}
        ticks_now: dict[int, int] = {}
        try:
            entries = os.listdir(self.proc_root)
        except OSError:
            entries = []
        for entry in entries:
            if not entry.isdigit():
                continue
            pid = int(entry)
            if pid == self._own_pid:
                continue  # 监控自己的命令行可能含 --wrap 的压测命令，不能被归进 locust/backend 组
            try:
                comm, _state, ticks, rss_pages = parse_proc_stat(_read(self._path(entry, "stat")))
                with open(self._path(entry, "cmdline"), "rb") as handle:
                    cmdline = handle.read().replace(b"\0", b" ").decode("utf-8", errors="replace").strip()
            except (OSError, ValueError, IndexError):
                continue  # 进程在读取过程中退出
            group = classify_process(comm, cmdline)
            if group is None:
                continue
            ticks_now[pid] = ticks
            bucket = groups[group]
            bucket["procs"] += 1
            bucket["rss"] += rss_pages * self.page_size / 1048576
            # 新出现的进程没有上一次的 ticks，不能把它的整个生命周期算进这一秒
            if dt and pid in self._prev_ticks:
                bucket["cpu"] += max(0, ticks - self._prev_ticks[pid]) / self.clk_tck / dt * 100
                bucket["seen"] = True
        self._prev_ticks = ticks_now
        return groups

    def sample(self, now: float) -> dict:
        dt = None if self._prev_time is None else max(now - self._prev_time, 1e-9)
        row: dict = {field: None for field in CSV_FIELDS}
        row["ts"] = round(now, 3)

        cpu = parse_cpu_times(_read(self._path("stat")))
        if self._prev_cpu is not None:
            for key, value in cpu_percent(self._prev_cpu, cpu).items():
                row[f"cpu_{key}"] = value
        self._prev_cpu = cpu

        row["load1"], row["load5"], row["load15"] = parse_loadavg(_read(self._path("loadavg")))
        mem = parse_meminfo(_read(self._path("meminfo")))
        row["mem_total_mb"] = mem.get("MemTotal", 0) / 1024
        row["mem_available_mb"] = mem.get("MemAvailable", 0) / 1024
        row["swap_used_mb"] = (mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)) / 1024

        vm = parse_vmstat(_read(self._path("vmstat")))
        if self._prev_vm is not None and dt:
            row["swapin_ps"] = max(0, vm.get("pswpin", 0) - self._prev_vm.get("pswpin", 0)) / dt
            row["swapout_ps"] = max(0, vm.get("pswpout", 0) - self._prev_vm.get("pswpout", 0)) / dt
        self._prev_vm = vm

        net = parse_net_dev(_read(self._path("net", "dev")))
        if self._prev_net is not None and dt:
            row["net_rx_bps"] = max(0, net["rx_bytes"] - self._prev_net["rx_bytes"]) / dt
            row["net_tx_bps"] = max(0, net["tx_bytes"] - self._prev_net["tx_bytes"]) / dt
        self._prev_net = net

        established = 0
        for name in ("tcp", "tcp6"):
            try:
                established += count_tcp_established(_read(self._path("net", name)))
            except OSError:
                pass
        row["tcp_established"] = established

        for name, bucket in self._processes(dt).items():
            row[f"{name}_cpu_pct"] = bucket["cpu"] if (dt and (bucket["seen"] or bucket["procs"] == 0)) else None
            row[f"{name}_rss_mb"] = bucket["rss"]
            row[f"{name}_procs"] = bucket["procs"]

        mysql = self._mysql(now)
        if mysql is not None:
            row["mysql_threads_connected"], row["mysql_slow_queries"] = mysql
        self._prev_time = now
        return row


def make_mysql_query(defaults_file: str) -> Callable[[], Optional[tuple[int, int]]]:
    """用 mysql 客户端读两个状态值；凭证只通过 --defaults-extra-file 传入，不接受命令行密码。"""

    def query() -> Optional[tuple[int, int]]:
        result = subprocess.run(
            [
                "mysql",
                f"--defaults-extra-file={defaults_file}",
                "-NBe",
                "SHOW GLOBAL STATUS WHERE Variable_name IN ('Threads_connected','Slow_queries')",
            ],
            capture_output=True, text=True, timeout=3, check=False,
        )
        if result.returncode != 0:
            return None
        values = {}
        for line in result.stdout.splitlines():
            name, _, value = line.partition("\t")
            if value.strip().isdigit():
                values[name] = int(value)
        if "Threads_connected" in values and "Slow_queries" in values:
            return values["Threads_connected"], values["Slow_queries"]
        return None

    return query


def _run_text(args: list[str]) -> Optional[str]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def collect_meta(proc_root: str, app_root: str, interval: float) -> dict:
    """启动时的环境快照；任何一项取不到都写 None，不阻断采样。"""
    try:
        mem = parse_meminfo(_read(os.path.join(proc_root, "meminfo")))
    except OSError:
        mem = {}
    systemd = None
    shown = _run_text(["systemctl", "show", "job-copilot", "-p", "MainPID", "-p", "ActiveState"])
    if shown:
        systemd = dict(line.split("=", 1) for line in shown.splitlines() if "=" in line)
    try:
        with open(os.path.abspath(__file__), "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()[:16]
    except OSError:
        digest = None
    return {
        "hostname": socket.gethostname(),
        "kernel": platform.release(),
        "cpu_count": os.cpu_count(),
        "mem_total_mb": round(mem["MemTotal"] / 1024) if "MemTotal" in mem else None,
        "swap_total_mb": round(mem["SwapTotal"] / 1024) if "SwapTotal" in mem else None,
        "systemd": systemd,
        "app_commit": _run_text(["git", "-C", app_root, "rev-parse", "--short=12", "HEAD"]),
        "interval_s": interval,
        "monitor_sha256": digest,
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


def _system_constants() -> tuple[int, int]:
    """(每秒 ticks, 页大小)；读不到时回退到 Linux 常见值。"""
    try:
        return os.sysconf("SC_CLK_TCK"), os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, ValueError, OSError):
        return 100, 4096


def _csv_value(value):
    if value is None:
        return ""
    return round(value, 3) if isinstance(value, float) else value


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="服务器资源采样器（读取 /proc，写 CSV）")
    parser.add_argument("--out", required=True, help="输出目录")
    parser.add_argument("--label", required=True, help="本轮标签，与压测的 -Label 保持一致")
    parser.add_argument("--interval", type=float, default=1.0, help="采样间隔秒数，默认 1")
    parser.add_argument("--duration", type=float, help="固定时长（秒）后退出")
    parser.add_argument("--wrap", help="运行这条命令，命令结束后停止采样并返回其退出码")
    parser.add_argument("--app-root", default="/opt/job-copilot", help="用于读取服务端 git 提交号")
    parser.add_argument("--mysql-defaults-file", help="mysql 客户端凭证文件（--defaults-extra-file）")
    parser.add_argument("--proc-root", default="/proc", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if not _LABEL_PATTERN.match(args.label):
        print("--label 只能包含字母、数字、点、下划线和连字符", file=sys.stderr)
        return 2
    if args.interval <= 0 or (args.duration is not None and args.duration <= 0):
        print("--interval 与 --duration 必须大于 0", file=sys.stderr)
        return 2
    if args.duration is not None and args.wrap:
        print("--duration 与 --wrap 不能同时使用", file=sys.stderr)
        return 2
    if not os.path.isdir(os.path.join(args.proc_root, "self")):
        print("需要 Linux 的 /proc；当前环境不可用，未生成任何文件。请在被测服务器上运行。", file=sys.stderr)
        return 2

    prefix = os.path.join(args.out, f"server-{args.label}")
    csv_path, meta_path = f"{prefix}_server.csv", f"{prefix}_server_meta.json"
    if os.path.exists(csv_path) or os.path.exists(meta_path):
        print(f"输出已存在：{csv_path}。请换一个 --label，不会覆盖旧数据。", file=sys.stderr)
        return 2
    os.makedirs(args.out, exist_ok=True)

    with open(meta_path, "w", encoding="utf-8") as handle:
        json.dump(collect_meta(args.proc_root, args.app_root, args.interval), handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    mysql_query = make_mysql_query(args.mysql_defaults_file) if args.mysql_defaults_file else None
    clk_tck, page_size = _system_constants()
    sampler = Sampler(proc_root=args.proc_root, clk_tck=clk_tck, page_size=page_size, mysql_query=mysql_query)
    child = None
    if args.wrap:
        if os.name == "nt":
            child = subprocess.Popen(args.wrap, shell=True)
        else:
            child = subprocess.Popen(shlex.split(args.wrap))

    started = time.time()
    next_at = started
    code = 0
    try:
        with open(csv_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            while True:
                now = time.time()
                row = sampler.sample(now)
                row["lag_ms"] = max(0.0, now - next_at) * 1000
                writer.writerow({key: _csv_value(value) for key, value in row.items()})
                handle.flush()
                if child is not None and child.poll() is not None:
                    break
                if args.duration is not None and now - started >= args.duration:
                    break
                next_at += args.interval
                time.sleep(max(0.0, next_at - time.time()))
    except KeyboardInterrupt:
        pass
    finally:
        if child is not None:
            if child.poll() is None:
                child.terminate()  # 被 Ctrl+C 或异常打断时不留下孤儿压测进程
            code = child.wait()
    return code


if __name__ == "__main__":
    sys.exit(main())
