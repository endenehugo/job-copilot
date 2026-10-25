"""AI 三阶段压测共用逻辑：开关校验、虚拟用户、响应契约检查和结果汇总。

阶段脚本只声明 Stage 参数并继承 AiChatUser；阶段之间不会自动升压。所有请求仍经过
support.py 的主机白名单、全局限速和熔断监视，本模块在其之上叠加阶段级限制：
用户数、每用户请求数、连续失败即停、HTTP 429 立即停。
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from collections import Counter
from dataclasses import dataclass
from typing import Callable

import gevent
import requests
from locust import HttpUser, events, task
from locust.exception import StopUser

if os.getenv("LT_ENABLE_AI", "").strip() != "1":
    raise RuntimeError(
        "AI 压测默认关闭。确认成本、配额与测试数据后，显式设置 LT_ENABLE_AI=1 再启动。"
    )

from support import (
    REQUEST_GATE,
    add_safety_options,
    check_envelope,
    configure_http_auth,
    ensure_test_allowed,
    install_safety_monitor,
    response_json,
    stop_test,
)

logger = logging.getLogger("loadtest.ai")

# 与后端 llm_request_timeout=60 对齐：单次模型调用超过这个耗时，说明上游超时/重试已经发生。
DEFAULT_SLOW_MS = {"memory": 60_000, "rag": 90_000, "agent": 100_000}
FALLBACK_MARKERS = ("这次模型没有返回内容", "这次没能给出完整回答")
DOWNLOAD_PREFIX = "/api/v1/conversation/file/"
CHAT_KEYS = ("answer", "original_answer", "conversation", "verification", "sources", "files")

MEMORY_QUERIES = (
    "用一句话回答：Python 的 GIL 是什么？",
    "用一句话回答：进程和线程的主要区别是什么？",
    "用一句话回答：TCP 为什么需要三次握手？",
    "用一句话回答：MySQL 索引为什么通常用 B+ 树？",
    "用一句话回答：Redis 的持久化有哪两种方式？",
)
# 会产生写入副作用：resume_generate_tool 会落盘 1 个 HTML、写 1 条简历生成记录并登记下载文件，
# 均随会话删除级联清理。素材取自上传的合成简历，查询里点名工具以减少模型自行选别的工具。
AGENT_QUERY = (
    "请根据我上传的简历材料，调用 resume_generate_tool 生成一份 HTML 简历，"
    "目标岗位是 Python 后端开发实习生，生成后把下载链接发给我。"
)
RESUME_DOC_NAME = "lt-resume-synthetic.txt"
RESUME_DOC_TEXT = (
    "压测合成简历（虚构人物，不含任何真实个人信息）\n\n"
    "姓名：周明远\n"
    "电话：000-0000-0000\n"
    "邮箱：lt-test@example.com\n"
    "求职意向：Python 后端开发实习生\n\n"
    "教育经历\n"
    "虚构理工大学 软件工程 本科 2023.09 - 2027.06\n\n"
    "实习经历\n"
    "星河科技（虚构公司） 后端开发实习生 2025.07 - 2025.10\n"
    "- 使用 FastAPI 和 MySQL 开发内部工单系统接口，负责 6 个核心接口的编码与单元测试。\n"
    "- 为工单列表接口增加索引与分页优化，接口平均耗时从 320 毫秒降到 90 毫秒。\n\n"
    "项目经历\n"
    "校园二手书交易平台 后端负责人 2025.03 - 2025.06\n"
    "- 使用 Python、FastAPI、Redis 实现下单、库存扣减与订单查询，支持 200 名内测用户使用。\n"
    "- 用 Redis 缓存热门书籍列表，缓存命中率约 85%。\n\n"
    "专业技能\n"
    "Python、FastAPI、MySQL、Redis、Git、Linux 基础\n"
)
RAG_DOC_NAME = "lt-rag-synthetic.txt"
RAG_DOC_TEXT = (
    "压测合成资料（虚构，不含任何真实个人信息）\n\n"
    "项目代号：星图-7421。\n"
    "负责人：虚构人物林远。\n"
    "核心指标：订单处理峰值 1830 笔/秒，P99 延迟 87 毫秒。\n"
    "技术栈：Go、Kafka、TiDB。\n"
    "上线日期：2031 年 2 月 14 日。\n"
)
RAG_UPLOAD_QUERY = "根据我上传的资料，项目“星图-7421”的订单处理峰值是多少？只回答数字和单位。"
RAG_UPLOAD_EXPECT = ("1830",)
RAG_BUILTIN_QUERY = "Redis 的 ZSet（有序集合）适合用在哪些场景？"
RAG_BUILTIN_EXPECT = ("排行榜",)


@dataclass(frozen=True)
class Stage:
    name: str
    modes: tuple[str, ...]
    min_users: int
    max_users: int
    default_users: int
    default_requests: int
    max_requests: int
    default_think: float
    default_run_time: str
    stop_after_failures: int
    health_after: bool = False
    health_probe_interval: float = 0.0
    confirm_above_users: int = 0
    confirm_env: str = ""


@dataclass(frozen=True)
class Settings:
    mode: str
    rag_source: str
    upload_doc: tuple[str, str] | None
    queries: tuple[str, ...]
    expect: tuple[str, ...]
    model: str
    token: str
    connect_timeout: float
    read_timeout: float
    aux_timeout: float
    requests_per_user: int
    think_time: float
    slow_ms: float
    stop_after_failures: int
    min_answer: int
    health_probe_interval: float


class RunState:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.outcomes: Counter[str] = Counter()
        self.latencies: list[float] = []
        self.slow = 0
        self.streak = 0
        self.finished_users = 0
        self.query_seq = 0
        self.kept_conversations: list[str] = []
        self.quitting = False
        self.summarized = False
        self.probe: gevent.Greenlet | None = None


RUN = RunState()


def _int_env(name: str, default: int, low: int, high: int) -> int:
    value = int(os.getenv(name, str(default)))
    if not low <= value <= high:
        raise ValueError(f"{name} 必须在 {low} 到 {high} 之间")
    return value


def _float_env(name: str, default: float, low: float, high: float) -> float:
    value = float(os.getenv(name, str(default)))
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} 必须在 {low} 到 {high} 之间")
    return value


def load_settings(stage: Stage) -> Settings:
    raw_mode = os.getenv("LT_MODE", "").strip().lower()
    if len(stage.modes) == 1:
        mode = stage.modes[0]
        if raw_mode and raw_mode != mode:
            raise ValueError(f"{stage.name} 只测 {mode} 模式，请取消或修正 LT_MODE={raw_mode}")
    else:
        mode = raw_mode
        if mode not in stage.modes:
            raise ValueError(f"{stage.name} 必须显式设置 LT_MODE 为 {' 或 '.join(stage.modes)}，且一次只测一种")
    if mode == "agent" and os.getenv("LT_ENABLE_AGENT", "").strip() != "1":
        raise RuntimeError("agent 可能触发外部工具；还必须显式设置 LT_ENABLE_AGENT=1")

    rag_source = ""
    if mode == "rag":
        rag_source = os.getenv("LT_RAG_SOURCE", "upload").strip().lower()
        if rag_source not in {"upload", "builtin"}:
            raise ValueError("LT_RAG_SOURCE 仅支持 upload（上传合成文档）或 builtin（内置知识库，不上传）")

    default_queries: tuple[str, ...] = MEMORY_QUERIES
    default_expect: tuple[str, ...] = ()
    upload_doc: tuple[str, str] | None = None
    if mode == "agent":
        default_queries = (AGENT_QUERY,)
        upload_doc = (RESUME_DOC_NAME, RESUME_DOC_TEXT)
    elif rag_source == "upload":
        default_queries, default_expect = (RAG_UPLOAD_QUERY,), RAG_UPLOAD_EXPECT
        upload_doc = (RAG_DOC_NAME, RAG_DOC_TEXT)
    elif rag_source == "builtin":
        default_queries, default_expect = (RAG_BUILTIN_QUERY,), RAG_BUILTIN_EXPECT

    custom_query = os.getenv("LT_QUERY", "").strip()
    queries = (custom_query,) if custom_query else default_queries
    # 自定义问题不沿用默认关键字；LT_EXPECT 显式给出（逗号分隔，命中任一即可），空串表示不检查。
    raw_expect = os.getenv("LT_EXPECT")
    if raw_expect is not None:
        expect = tuple(item.strip() for item in raw_expect.split(",") if item.strip())
    else:
        expect = () if custom_query else default_expect

    read_timeout = _float_env("LT_AI_READ_TIMEOUT", 120, 5, 300)
    probe_interval = float(os.getenv("LT_HEALTH_PROBE_INTERVAL", str(stage.health_probe_interval)))
    if probe_interval != 0 and not 5 <= probe_interval <= 60:
        raise ValueError("LT_HEALTH_PROBE_INTERVAL 必须为 0（关闭）或 5 到 60 秒")

    return Settings(
        mode=mode,
        rag_source=rag_source,
        upload_doc=upload_doc,
        queries=queries,
        expect=expect,
        model=os.getenv("LT_MODEL", "").strip(),
        token=os.getenv("LT_ADMIN_TOKEN", os.getenv("LT_TOKEN", "")).strip(),
        connect_timeout=_float_env("LT_CONNECT_TIMEOUT", 5, 1, 30),
        read_timeout=read_timeout,
        aux_timeout=_float_env("LT_READ_TIMEOUT", 15, 1, 60),
        requests_per_user=_int_env("LT_AI_MAX_REQUESTS_PER_USER", stage.default_requests, 1, stage.max_requests),
        think_time=_float_env("LT_AI_THINK_TIME", stage.default_think, 0, 60),
        slow_ms=_float_env("LT_SLOW_MS", DEFAULT_SLOW_MS[mode], 1000, 300_000),
        stop_after_failures=_int_env("LT_AI_STOP_AFTER_FAILURES", stage.stop_after_failures, 1, 10),
        min_answer=_int_env("LT_MIN_ANSWER", 5, 1, 1000),
        health_probe_interval=probe_interval,
    )


def install(stage: Stage) -> Settings:
    """注册阶段默认值、阶段级安全校验、健康探针与结果汇总，返回本阶段配置。"""
    settings = load_settings(stage)
    add_safety_options(
        "ai",
        ("ai",),
        default_max_rps=0.5,
        rate_env_var="LT_AI_MAX_RPS",
        max_spawn_rate=1.0,
        max_allowed_rps=1.0,
    )

    @events.init_command_line_parser.add_listener
    def stage_defaults(parser, **kwargs) -> None:
        # 即使环境里残留 LT_SCENARIO=basic，也强制走 AI 场景的更严格上限。
        parser.set_defaults(
            scenario="ai",
            num_users=int(os.getenv("LT_USERS", str(stage.default_users))),
            run_time=os.getenv("LT_RUN_TIME", stage.default_run_time),
            # 到时间时让在途的模型请求自然结束（或触发客户端超时），而不是被静默杀掉而不计入统计。
            stop_timeout=int(settings.read_timeout + settings.connect_timeout + 10),
        )

    install_safety_monitor()

    @events.test_start.add_listener
    def validate_stage(environment, **kwargs) -> None:
        RUN.reset()
        options = environment.parsed_options
        if not _is_allowed(environment):
            return  # support.py 已拒绝并给出原因

        users = int(options.num_users)
        problem = None
        if not stage.min_users <= users <= stage.max_users:
            problem = f"{stage.name} 用户数必须在 {stage.min_users} 到 {stage.max_users} 之间，当前 {users}"
        elif (
            stage.confirm_above_users
            and users > stage.confirm_above_users
            and os.getenv(stage.confirm_env, "").strip().upper() != "YES"
        ):
            problem = f"超过 {stage.confirm_above_users} 个用户需确认成本、配额和工具副作用后设置 {stage.confirm_env}=YES"
        if problem:
            stop_test(environment, problem)
            return

        logger.info(
            "阶段=%s mode=%s users=%s 每用户最多 chat %s 次（合计 ≤%s 次）model=%s slow>%sms 连续失败 %s 次即停",
            stage.name,
            settings.mode,
            users,
            settings.requests_per_user,
            users * settings.requests_per_user,
            settings.model or "服务端默认",
            int(settings.slow_ms),
            settings.stop_after_failures,
        )
        if settings.health_probe_interval:
            RUN.probe = gevent.spawn(_probe_loop, environment, settings)

    @events.test_stop.add_listener
    def summarize(environment, **kwargs) -> None:
        # 到达 --run-time 时 Locust 可能触发两次 test_stop，只汇总一次。
        if RUN.summarized:
            return
        RUN.summarized = True
        if RUN.probe is not None:
            RUN.probe.kill(block=False)
            RUN.probe = None
        summary = build_summary(stage, settings, int(environment.parsed_options.num_users))
        logger.info("\n%s", format_summary(summary))
        prefix = getattr(environment.parsed_options, "csv_prefix", None)
        if prefix:
            with open(f"{prefix}_ai_summary.json", "w", encoding="utf-8") as handle:
                json.dump(summary, handle, ensure_ascii=False, indent=2)

    return settings


def _is_allowed(environment) -> bool:
    try:
        ensure_test_allowed(environment)
    except RuntimeError:
        return False
    return True


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def build_summary(stage: Stage, settings: Settings, users: int) -> dict:
    chat = list(RUN.latencies)
    summary: dict = {
        "stage": stage.name,
        "mode": settings.mode,
        "users": users,
        "chat_requests": len(chat),
        "outcomes": dict(RUN.outcomes),
        "slow_over_budget": RUN.slow,
        "slow_budget_ms": int(settings.slow_ms),
    }
    if RUN.kept_conversations:
        summary["kept_conversations"] = list(RUN.kept_conversations)
    if chat:
        summary["latency_ms"] = {
            "min": round(min(chat)),
            "p50": round(_percentile(chat, 0.50)),
            "p95": round(_percentile(chat, 0.95)),
            "max": round(max(chat)),
        }
    return summary


def format_summary(summary: dict) -> str:
    lines = [
        f"===== {summary['stage']} 汇总（mode={summary['mode']}，users={summary['users']}）=====",
        f"chat 请求数：{summary['chat_requests']}；超过 {summary['slow_budget_ms']}ms 预算：{summary['slow_over_budget']}",
    ]
    outcomes = summary["outcomes"]
    if outcomes:
        lines.append("结果分类：" + "，".join(f"{key}={value}" for key, value in sorted(outcomes.items())))
    latency = summary.get("latency_ms")
    if latency:
        note = "（样本不足 20，分位数仅供参考）" if summary["chat_requests"] < 20 else ""
        lines.append(
            f"端到端延迟 ms：min={latency['min']} P50={latency['p50']} P95={latency['p95']} max={latency['max']}{note}"
        )
    kept = summary.get("kept_conversations")
    if kept:
        lines.append(
            "未自动删除的会话（客户端超时，后端可能仍在落盘文件，待其结束后手动清理）：" + ", ".join(kept)
        )
    lines.append("说明：延迟含公网 RTT 与上游模型耗时；服务器资源需按 README 另行采集。")
    return "\n".join(lines)


def _probe_loop(environment, settings: Settings) -> None:
    """低频探测 /system/health：模型请求占满后端线程时，健康接口延迟会先变差。

    探针不经过全局限速闸门（避免挤占模型请求的发起间隔），频率被限制在每 5 秒 1 次以内。
    """
    session = requests.Session()
    session.trust_env = False
    configure_http_auth(session)
    if settings.token:
        session.headers.update({"X-Admin-Token": settings.token})
    url = environment.parsed_options.host.rstrip("/") + "/api/v1/system/health"
    while True:
        gevent.sleep(settings.health_probe_interval)
        started = time.perf_counter()
        error: Exception | None = None
        length = 0
        try:
            response = session.get(url, timeout=(settings.connect_timeout, settings.aux_timeout))
            length = len(response.content)
            body = response_json(response)
            reason = check_envelope(response, body)
            if reason is None and (body.get("data") or {}).get("status") != "ok":
                reason = "E_CONTRACT|health.status"
            if reason:
                error = RuntimeError(reason)
        except requests.RequestException as exc:
            error = exc
        environment.events.request.fire(
            request_type="GET",
            name="[probe] GET /system/health",
            response_time=(time.perf_counter() - started) * 1000,
            response_length=length,
            response=None,
            context={},
            exception=error,
        )


def _request_quit(environment, message: str | None = None) -> None:
    """请求停止测试：带 message 走安全停止（非零退出码），否则是配额用完后的正常结束。"""
    if RUN.quitting or environment.runner is None:
        return
    RUN.quitting = True
    if message:
        stop_test(environment, message)
    else:
        gevent.spawn(environment.runner.quit)


def _record(environment, settings: Settings, kind: str, reason: str | None, elapsed_ms: float) -> None:
    if kind == "chat":
        RUN.latencies.append(elapsed_ms)
        if elapsed_ms > settings.slow_ms:
            RUN.slow += 1
    category = "OK" if reason is None else reason.split("|", 1)[0]
    RUN.outcomes[category if kind == "chat" else f"{kind}:{category}"] += 1
    if reason is None:
        # 只有成功的 chat 才打断“连续失败”计数；别的用户建会话成功不能掩盖模型侧连续失败。
        if kind == "chat":
            RUN.streak = 0
        return

    RUN.streak += 1
    logger.warning("%s 失败：%s（耗时 %.0fms）", kind, reason, elapsed_ms)
    if category == "E_HTTP_429":
        _request_quit(environment, "收到 HTTP 429（限流或额度），立即停止以免继续消耗配额")
    elif RUN.streak >= settings.stop_after_failures:
        _request_quit(environment, f"连续 {RUN.streak} 次失败，停止测试（最近：{reason}）")


def transport_error(response) -> str | None:
    error = getattr(response, "error", None)
    if error is None:
        return None
    if isinstance(error, requests.exceptions.Timeout):
        return f"E_TIMEOUT|{type(error).__name__}"
    if isinstance(error, requests.exceptions.ConnectionError):
        return "E_CONN|connection failed"
    return f"E_TRANSPORT|{type(error).__name__}"


def _contract_error(body: dict, data: object, conversation_id: str, query: str) -> str | None:
    if not isinstance(data, dict):
        return "E_CONTRACT|data"
    missing = [key for key in CHAT_KEYS if key not in data]
    if missing:
        return "E_CONTRACT|missing " + ",".join(missing)
    if not isinstance(data["sources"], list) or not isinstance(data["files"], list):
        return "E_CONTRACT|sources/files type"
    if body.get("message") != data["answer"]:
        return "E_CONTRACT|envelope message != data.answer"
    conversation = data["conversation"]
    if not isinstance(conversation, dict) or conversation.get("conversation_id") != conversation_id:
        return "E_CONTRACT|conversation.conversation_id"
    messages = conversation.get("messages")
    if not isinstance(messages, list) or len(messages) < 2:
        return "E_CONTRACT|conversation.messages"
    tail = {
        (str(item.get("role")), str(item.get("content") or "").strip())
        for item in messages[-2:]
        if isinstance(item, dict)
    }
    expected = {("user", query.strip()), ("assistant", str(data["original_answer"]).strip())}
    if tail != expected:
        return "E_CONTRACT|conversation.messages not persisted"
    return None


def _artifact_error(files: list) -> str | None:
    """Agent 阶段的预期副作用：恰好 1 个可下载的 HTML 简历，多于 1 个说明工具被重复/越权调用。"""
    if not files:
        return "E_ARTIFACT|resume_generate_tool produced no file"
    if len(files) > 1:
        return "E_SIDE_EFFECT|more than one file generated"
    item = files[0]
    if not (
        isinstance(item, dict)
        and item.get("format") == "html"
        and str(item.get("url") or "").startswith(DOWNLOAD_PREFIX)
        and item.get("size", 0) > 0
    ):
        return "E_CONTRACT|file artifact fields"
    return None


def _mode_error(settings: Settings, data: dict) -> str | None:
    sources, files = data["sources"], data["files"]
    if settings.mode == "memory":
        if sources or files or data["verification"] is not None:
            return "E_CONTRACT|memory returned sources/files/verification"
    elif settings.mode == "rag":
        if files:
            return "E_SIDE_EFFECT|rag generated files"
        if not sources:
            return "E_SOURCES|no retrieval sources"
        if settings.rag_source == "upload" and not any(
            isinstance(item, dict) and item.get("source_name") == RAG_DOC_NAME for item in sources
        ):
            return "E_SOURCES|uploaded document not retrieved"
    elif settings.mode == "agent":
        return _artifact_error(files)
    return None


class AiChatUser(HttpUser):
    """每个虚拟用户独占一个会话；按阶段配额发 chat，结束时删除会话。"""

    abstract = True
    stage: Stage
    settings: Settings

    def wait_time(self) -> float:
        return self.settings.think_time

    def on_start(self) -> None:
        self.conversation_id = ""
        self.keep_conversation = False
        self.sent = 0
        if not _is_allowed(self.environment):
            raise StopUser()  # 拒绝原因已在 test_start 记录，这里不再发送任何业务请求
        self.client.trust_env = False
        configure_http_auth(self.client)
        if self.settings.token:
            self.client.headers.update({"X-Admin-Token": self.settings.token})

        def valid_id(data: object) -> str | None:
            ok = isinstance(data, dict) and isinstance(data.get("conversation_id"), str) and data["conversation_id"]
            return None if ok else "E_CONTRACT|conversation.create"

        reason, data = self.call_aux(
            "POST",
            "/api/v1/conversation/create",
            "[setup] POST /conversation/create",
            validate=valid_id,
            json={"title": "lt-session", "mode": self.settings.mode},
        )
        if reason is not None:
            raise StopUser()
        self.conversation_id = data["conversation_id"]
        self.prepare_conversation()

    def prepare_conversation(self) -> None:
        """阶段脚本可覆盖：在发 chat 前准备会话数据（失败时抛 StopUser）。"""

    def call_aux(
        self,
        method: str,
        path: str,
        name: str,
        *,
        validate: Callable[[object], str | None] | None = None,
        timeout: float | None = None,
        raw: bool = False,
        **kwargs,
    ) -> tuple[str | None, object]:
        """setup / cleanup / check 请求：返回 (失败原因, data)，失败会同时计入熔断。

        raw=True 用于文件下载这类不返回 JSON 信封的接口：只检查 HTTP 200，validate 收到响应文本。
        """
        REQUEST_GATE.wait()
        started = time.perf_counter()
        with self.client.request(
            method,
            path,
            name=name,
            timeout=(self.settings.connect_timeout, timeout or self.settings.aux_timeout),
            catch_response=True,
            **kwargs,
        ) as response:
            if raw:
                reason = transport_error(response)
                if reason is None and response.status_code != 200:
                    reason = f"E_HTTP_{response.status_code}|download"
                data = response.text if reason is None else ""
            else:
                body = response_json(response)
                reason = transport_error(response) or check_envelope(response, body)
                data = body.get("data") if body else None
            if reason is None and validate is not None:
                reason = validate(data)
            if reason is not None:
                response.failure(reason)
        kind = name.split("]", 1)[0].lstrip("[") if name.startswith("[") else "aux"
        _record(self.environment, self.settings, kind, reason, (time.perf_counter() - started) * 1000)
        return reason, data

    @task
    def chat(self) -> None:
        settings = self.settings
        query = settings.queries[RUN.query_seq % len(settings.queries)]
        RUN.query_seq += 1
        payload = {"conversation_id": self.conversation_id, "query": query, "mode": settings.mode}
        if settings.model:
            payload["model"] = settings.model

        # 按“尝试次数”计数：超时或连接失败也不会形成无限重试/计费循环。
        REQUEST_GATE.wait()
        # 限速等待期间若已触发停止（连续失败、429、support 熔断），不再发起新的付费请求。
        if RUN.quitting or not _is_allowed(self.environment):
            raise StopUser()
        self.sent += 1
        started = time.perf_counter()
        self.last_data: dict | None = None
        with self.client.post(
            "/api/v1/conversation/chat",
            json=payload,
            name=f"POST /conversation/chat [{settings.mode}]",
            timeout=(settings.connect_timeout, settings.read_timeout),
            catch_response=True,
        ) as response:
            elapsed_ms = (time.perf_counter() - started) * 1000
            reason = self._chat_error(response, query, elapsed_ms)
            if reason is not None:
                response.failure(reason)
        _record(self.environment, settings, "chat", reason, elapsed_ms)

        if reason is None:
            self.after_chat_ok(self.last_data)
        elif reason.startswith("E_TIMEOUT") and settings.mode == "agent":
            # 客户端放弃后后端可能仍在跑工具：此刻删会话，文件稍后才落盘就成了无主文件。
            self.keep_conversation = True
            RUN.kept_conversations.append(self.conversation_id)
            logger.warning("agent 请求超时，保留会话 %s，请待后端结束后手动清理", self.conversation_id)

        if self.sent >= settings.requests_per_user:
            raise StopUser()

    def after_chat_ok(self, data: dict | None) -> None:
        """chat 成功后的附加校验；默认只有 Agent 会核对生成的文件真的能下载。"""
        if self.settings.mode != "agent" or not data:
            return
        self.call_aux(
            "GET",
            data["files"][0]["url"],
            "[check] GET /conversation/file/{filename}",
            raw=True,
            validate=lambda text: None
            if "<html" in str(text).lower()
            else "E_ARTIFACT|downloaded file is not html",
        )

    def _chat_error(self, response, query: str, elapsed_ms: float) -> str | None:
        settings = self.settings
        reason = transport_error(response)
        if reason is not None:
            return reason
        body = response_json(response)
        reason = check_envelope(response, body)
        if reason is not None:
            return reason
        if body.get("degraded"):
            return "E_DEGRADED|" + str((body.get("warnings") or ["degraded"])[0])[:80]
        data = body.get("data")
        reason = _contract_error(body, data, self.conversation_id, query)
        if reason is not None:
            return reason
        self.last_data = data

        answer = str(data["answer"]).strip()
        if len(answer) < settings.min_answer:
            return "E_EMPTY|answer too short"
        marker = next((item for item in FALLBACK_MARKERS if item in answer), None)
        if marker:
            return f"E_FALLBACK|{marker}"
        reason = _mode_error(settings, data)
        if reason is not None:
            return reason
        if settings.expect:
            normalized = answer.replace(",", "").replace(" ", "")
            if not any(keyword in normalized for keyword in settings.expect):
                return "E_CONTENT|expected keyword missing"
        if elapsed_ms > settings.slow_ms:
            return "E_SLOW|latency over budget"
        return None

    def on_stop(self) -> None:
        if self.stage.health_after and self.sent:
            # 模型请求（含超时）之后服务本身仍应能响应轻量请求。
            self.call_aux(
                "GET",
                "/api/v1/system/health",
                "[check] GET /system/health",
                validate=lambda data: None
                if isinstance(data, dict) and data.get("status") == "ok"
                else "E_CONTRACT|health.status",
            )
        if self.conversation_id and not self.keep_conversation:
            self.call_aux(
                "POST",
                "/api/v1/conversation/delete",
                "[cleanup] POST /conversation/delete",
                json={"conversation_id": self.conversation_id},
            )
        self.conversation_id = ""

        RUN.finished_users += 1
        if RUN.finished_users >= int(self.environment.parsed_options.num_users):
            _request_quit(self.environment)
