"""显式启用的 AI 接口低并发压测：POST /api/v1/conversation/chat。

默认拒绝启动。先设置 LT_ENABLE_AI=1；每个虚拟用户默认只发 1 次模型请求，
最多 2 个用户、运行不超过 120 秒。agent 工具模式还需单独设置
LT_ENABLE_AGENT=1。请勿与轻量接口混跑，模型延迟包含外部供应商耗时。
"""

from __future__ import annotations

import os

from locust import HttpUser, constant, task
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
)


MODE = os.getenv("LT_MODE", "memory").strip().lower()
QUERY = os.getenv("LT_QUERY", "用一句话回答：Python 的 GIL 是什么？").strip()
TOKEN = os.getenv("LT_ADMIN_TOKEN", os.getenv("LT_TOKEN", "")).strip()
MODEL = os.getenv("LT_MODEL", "").strip()
CONNECT_TIMEOUT = float(os.getenv("LT_CONNECT_TIMEOUT", "5"))
READ_TIMEOUT = float(os.getenv("LT_AI_READ_TIMEOUT", "200"))
MAX_REQUESTS_PER_USER = int(os.getenv("LT_AI_MAX_REQUESTS_PER_USER", "1"))
MIN_ANSWER_CHARS = int(os.getenv("LT_MIN_ANSWER", "5"))
FALLBACK_MARKERS = ("这次模型没有返回内容", "这次没能给出完整回答")

if MODE not in {"memory", "rag", "agent"}:
    raise ValueError("LT_MODE 仅支持 memory、rag、agent")
if MODE == "agent" and os.getenv("LT_ENABLE_AGENT", "").strip() != "1":
    raise RuntimeError("agent 可能触发外部工具；还必须显式设置 LT_ENABLE_AGENT=1")
if not QUERY:
    raise ValueError("LT_QUERY 不能为空")
if not 1 <= MAX_REQUESTS_PER_USER <= 3:
    raise ValueError("LT_AI_MAX_REQUESTS_PER_USER 必须在 1 到 3 之间")

add_safety_options(
    "ai",
    ("ai",),
    default_max_rps=0.5,
    rate_env_var="LT_AI_MAX_RPS",
    max_spawn_rate=1.0,
    max_allowed_rps=1.0,
)
install_safety_monitor()


class ChatUser(HttpUser):
    wait_time = constant(1)

    def on_start(self) -> None:
        ensure_test_allowed(self.environment)
        self.client.trust_env = False
        self.conversation_id = ""
        self.requests_sent = 0
        configure_http_auth(self.client)
        if TOKEN:
            self.client.headers.update({"X-Admin-Token": TOKEN})

        REQUEST_GATE.wait()
        with self.client.post(
            "/api/v1/conversation/create",
            json={"title": "lt-session", "mode": MODE},
            name="[setup] POST /conversation/create",
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            catch_response=True,
        ) as response:
            body = response_json(response)
            reason = check_envelope(response, body)
            data = body.get("data") if body else None
            if reason is None and not (
                isinstance(data, dict)
                and isinstance(data.get("conversation_id"), str)
                and data["conversation_id"]
            ):
                reason = "E_CONTRACT|conversation.create"
            if reason is not None:
                response.failure(reason)
                raise StopUser()
            self.conversation_id = data["conversation_id"]

    @task
    def chat_once_or_limited(self) -> None:
        if self.requests_sent >= MAX_REQUESTS_PER_USER:
            return

        payload: dict[str, str] = {
            "conversation_id": self.conversation_id,
            "query": QUERY,
            "mode": MODE,
        }
        if MODEL:
            payload["model"] = MODEL

        # 将请求次数按“尝试次数”计，连接失败也不能形成无限重试/计费循环。
        self.requests_sent += 1
        REQUEST_GATE.wait()
        with self.client.post(
            "/api/v1/conversation/chat",
            json=payload,
            name=f"POST /conversation/chat [{MODE}]",
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            catch_response=True,
        ) as response:
            body = response_json(response)
            reason = check_envelope(response, body)
            data = body.get("data") if body else None
            answer = str(data.get("answer") or "").strip() if isinstance(data, dict) else ""
            if reason is None and len(answer) < MIN_ANSWER_CHARS:
                reason = f"E_EMPTY|answer chars={len(answer)}"
            if reason is None:
                marker = next((item for item in FALLBACK_MARKERS if item in answer), None)
                if marker:
                    reason = f"E_FALLBACK|{marker}"
            if reason is None and body.get("degraded"):
                reason = "E_DEGRADED|" + str((body.get("warnings") or ["degraded"])[0])[:80]
            if reason is not None:
                response.failure(reason)

    def on_stop(self) -> None:
        if not self.conversation_id:
            return
        REQUEST_GATE.wait()
        with self.client.post(
            "/api/v1/conversation/delete",
            json={"conversation_id": self.conversation_id},
            name="[cleanup] POST /conversation/delete",
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            catch_response=True,
        ) as response:
            body = response_json(response)
            reason = check_envelope(response, body)
            if reason is not None:
                response.failure(reason)
        self.conversation_id = ""
