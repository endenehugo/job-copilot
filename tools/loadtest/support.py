"""Locust 公共安全限制、响应检查和可选全局请求限速。"""

from __future__ import annotations

import logging
import math
import os
import re
import time
from collections import deque
from urllib.parse import urlsplit

from gevent import sleep, spawn
from gevent.lock import Semaphore
from locust import events

logger = logging.getLogger("loadtest")

_ALLOWED_HOSTS = {"displaydemo.top", "www.displaydemo.top", "47.92.74.156", "localhost", "127.0.0.1", "::1"}
_DURATION_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smh]?)\s*$", re.IGNORECASE)
_APPROVED = False
_ACTIVE_ENVIRONMENT = None
_ABORTED = False
_RECENT_FAILURES: deque[bool] = deque()
_RECENT_LATENCIES: deque[float] = deque()
_CONSECUTIVE_5XX = 0
_ABORT_WINDOW = 50
_ABORT_FAILURE_RATE = 0.10
_ABORT_5XX_STREAK = 3
_ABORT_P95_MS = 2000.0


class RequestRateGate:
    """单 Locust 进程内的全局间隔限速器；0 表示关闭。"""

    def __init__(self) -> None:
        self._rate = 0.0
        self._next_at = 0.0
        self._lock = Semaphore()

    def configure(self, rate: float) -> None:
        self._rate = rate
        self._next_at = 0.0

    def wait(self) -> None:
        if self._rate <= 0:
            return
        with self._lock:
            now = time.monotonic()
            allowed_at = max(now, self._next_at)
            self._next_at = allowed_at + 1.0 / self._rate
        delay = allowed_at - now
        if delay > 0:
            sleep(delay)


REQUEST_GATE = RequestRateGate()


def response_json(response) -> dict | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def check_envelope(response, body: dict | None) -> str | None:
    """HTTP 状态和项目 JSON 信封都检查，业务失败不因 HTTP 200 被算作成功。"""
    status = response.status_code
    if status == 429:
        return "E_HTTP_429|rate limited"
    if status in {401, 403, 404, 422}:
        return f"E_HTTP_{status}|unexpected response"
    if status >= 500:
        return f"E_HTTP_5XX|{status}"
    if status < 200 or status >= 300:
        return f"E_HTTP_{status}"
    if body is None:
        return "E_HTTP_BODY|non-JSON response"
    if body.get("code") != "success":
        return f"E_BUSINESS|{status}|{body.get('code')}|{str(body.get('message') or '')[:80]}"
    return None


def configure_http_auth(client) -> None:
    username = os.getenv("LT_BASIC_AUTH_USER", "").strip()
    password = os.getenv("LT_BASIC_AUTH_PASSWORD", "")
    if bool(username) != bool(password):
        raise ValueError("LT_BASIC_AUTH_USER 与 LT_BASIC_AUTH_PASSWORD 必须同时设置")
    if username:
        client.auth = (username, password)


def add_safety_options(
    default_scenario: str,
    scenarios: tuple[str, ...],
    *,
    default_max_rps: float,
    rate_env_var: str = "LT_MAX_RPS",
    max_spawn_rate: float = 5.0,
    max_allowed_rps: float = 30.0,
) -> None:
    """为 Locust CLI 增加场景/限速选项，并为单阶段设置保守默认值。"""

    def add_options(parser) -> None:
        parser.add_argument(
            "--scenario",
            choices=scenarios,
            default=os.getenv("LT_SCENARIO", default_scenario),
            help="选择单次测试场景；不会自动切换并发阶段",
        )
        parser.add_argument(
            "--max-rps",
            type=float,
            default=float(os.getenv(rate_env_var, str(default_max_rps))),
            help="单 Locust 进程的全局请求启动速率上限；0 表示不限",
        )
        parser.add_argument(
            "--confirm-40-users",
            action="store_true",
            default=os.getenv("LT_CONFIRM_40_USERS", "").strip().upper() == "YES",
            help="明确确认后才允许运行 31–40 用户阶段",
        )
        parser.set_defaults(
            host=os.getenv("LT_HOST", "https://displaydemo.top"),
            num_users=int(os.getenv("LT_USERS", "1")),
            spawn_rate=float(os.getenv("LT_SPAWN_RATE", "1")),
            run_time=os.getenv("LT_RUN_TIME", "60s"),
            lt_max_spawn_rate=max_spawn_rate,
            lt_max_allowed_rps=max_allowed_rps,
        )

    events.init_command_line_parser.add_listener(add_options)


def _duration_seconds(value: str | None) -> float | None:
    if not value:
        return None
    match = _DURATION_PATTERN.fullmatch(str(value))
    if not match:
        return None
    amount = float(match.group(1))
    multiplier = {"": 1, "s": 1, "m": 60, "h": 3600}[match.group(2).lower()]
    return amount * multiplier


def _stop(environment, message: str) -> None:
    global _APPROVED, _ABORTED
    _APPROVED = False
    _ABORTED = True
    logger.error("压测安全停止：%s", message)
    # 安全停止不应以退出码 0 结束，否则调用方会把“被拒绝/被熔断”误读成通过。
    environment.process_exit_code = 1
    runner = getattr(environment, "runner", None)
    if runner is not None:
        # test_start 与 request 回调都跑在 Locust 管理的 greenlet 里，直接 quit() 会把当前
        # greenlet 自己杀掉，导致在 test_start 里被拒绝时空等到 --run-time；放到独立 greenlet 执行。
        spawn(runner.quit)


def stop_test(environment, message: str) -> None:
    """供阶段脚本在附加安全检查失败时撤销批准并停止本次测试。"""
    _stop(environment, message)


def ensure_test_allowed(environment) -> None:
    if not _APPROVED:
        raise RuntimeError("本次测试未通过安全限制，未发送业务请求")


def install_safety_monitor() -> None:
    @events.test_start.add_listener
    def validate_test_start(environment, **kwargs) -> None:
        global _ACTIVE_ENVIRONMENT, _APPROVED, _ABORTED
        global _RECENT_FAILURES, _RECENT_LATENCIES, _CONSECUTIVE_5XX
        global _ABORT_WINDOW, _ABORT_FAILURE_RATE, _ABORT_5XX_STREAK, _ABORT_P95_MS

        _ACTIVE_ENVIRONMENT = environment
        _APPROVED = False
        _ABORTED = False
        _RECENT_FAILURES = deque()
        _RECENT_LATENCIES = deque()
        _CONSECUTIVE_5XX = 0
        options = environment.parsed_options

        host = str(getattr(options, "host", "") or "").strip()
        try:
            hostname = (urlsplit(host).hostname or "").lower()
        except ValueError:
            hostname = ""
        if hostname not in _ALLOWED_HOSTS:
            _stop(environment, "目标主机不在白名单内（仅 displaydemo.top、指定服务器 IP 或本机）")
            return

        try:
            users = int(options.num_users)
            spawn_rate = float(options.spawn_rate)
            max_rps = float(options.max_rps)
            max_spawn_rate = float(options.lt_max_spawn_rate)
            max_allowed_rps = float(options.lt_max_allowed_rps)
            _ABORT_WINDOW = max(1, int(os.getenv("LT_ABORT_WINDOW", "50")))
            _ABORT_FAILURE_RATE = float(os.getenv("LT_ABORT_FAILURE_RATE", "0.10"))
            _ABORT_5XX_STREAK = max(1, int(os.getenv("LT_ABORT_5XX_STREAK", "3")))
            default_p95 = "30000" if options.scenario == "ai" else "2000"
            _ABORT_P95_MS = float(os.getenv("LT_ABORT_P95_MS", default_p95))
        except (AttributeError, TypeError, ValueError) as exc:
            _stop(environment, f"并发或安全阈值配置无效：{exc}")
            return

        if (
            users < 1
            or not math.isfinite(spawn_rate)
            or spawn_rate <= 0
            or spawn_rate > max_spawn_rate
            or not math.isfinite(max_rps)
            or max_rps < 0
            or max_rps > max_allowed_rps
        ):
            _stop(
                environment,
                f"spawn-rate 须在 (0, {max_spawn_rate}]，max-rps 须在 [0, {max_allowed_rps}]",
            )
            return
        if max_rps == 0 and hostname not in {"localhost", "127.0.0.1", "::1"}:
            _stop(environment, "远程目标必须设置正的 max-rps 限速")
            return
        if (
            not math.isfinite(_ABORT_FAILURE_RATE)
            or not math.isfinite(_ABORT_P95_MS)
            or not 0 <= _ABORT_FAILURE_RATE <= 1
            or _ABORT_P95_MS < 0
        ):
            _stop(environment, "失败率阈值须在 0–1 内，P95 阈值须为非负数")
            return

        max_users = 2 if options.scenario == "ai" else 30
        if users > max_users:
            if options.scenario == "ai" or users > 40 or not options.confirm_40_users:
                _stop(environment, f"{options.scenario} 场景最多 {max_users} 个用户；31–40 需单独人工确认")
                return

        duration = _duration_seconds(getattr(options, "run_time", None))
        max_duration = 120 if options.scenario == "ai" else 300
        if duration is None or duration <= 0 or duration > max_duration:
            _stop(environment, f"必须设置有限测试时长；{options.scenario} 场景上限 {max_duration} 秒")
            return

        REQUEST_GATE.configure(max_rps)
        _APPROVED = True
        logger.info(
            "场景=%s host=%s users=%s duration=%ss max_rps=%s",
            options.scenario,
            hostname,
            users,
            duration,
            max_rps or "unlimited",
        )

    @events.request.add_listener
    def monitor_request(
        request_type=None,
        name=None,
        response_time=None,
        response=None,
        exception=None,
        **kwargs,
    ) -> None:
        global _CONSECUTIVE_5XX
        if not _APPROVED or _ABORTED or _ACTIVE_ENVIRONMENT is None:
            return

        status = int(getattr(response, "status_code", 0) or 0)
        failed = exception is not None
        _RECENT_FAILURES.append(failed)
        _RECENT_LATENCIES.append(float(response_time or 0))
        while len(_RECENT_FAILURES) > _ABORT_WINDOW:
            _RECENT_FAILURES.popleft()
        while len(_RECENT_LATENCIES) > _ABORT_WINDOW:
            _RECENT_LATENCIES.popleft()

        _CONSECUTIVE_5XX = _CONSECUTIVE_5XX + 1 if status >= 500 else 0
        if _CONSECUTIVE_5XX >= _ABORT_5XX_STREAK:
            _stop(_ACTIVE_ENVIRONMENT, f"连续 {_CONSECUTIVE_5XX} 次 HTTP 5xx")
            return

        if (
            len(_RECENT_FAILURES) >= min(10, _ABORT_WINDOW)
            and _ABORT_FAILURE_RATE > 0
            and sum(_RECENT_FAILURES) / len(_RECENT_FAILURES) > _ABORT_FAILURE_RATE
        ):
            _stop(_ACTIVE_ENVIRONMENT, f"最近 {len(_RECENT_FAILURES)} 个请求失败率超过阈值")
            return

        if _ABORT_P95_MS > 0 and len(_RECENT_LATENCIES) >= min(20, _ABORT_WINDOW):
            ordered = sorted(_RECENT_LATENCIES)
            p95 = ordered[math.ceil(0.95 * len(ordered)) - 1]
            if p95 > _ABORT_P95_MS:
                _stop(_ACTIVE_ENVIRONMENT, f"最近请求滚动 P95 {p95:.0f}ms 超过阈值")
