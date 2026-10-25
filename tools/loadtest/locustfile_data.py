"""安全的轻量 HTTP 压测：只覆盖无模型调用的数据接口。

运行场景由 --scenario basic|business 选择。每个虚拟用户在初始化时创建自己的
memory 会话，结束时尝试通过 API 删除；初始化与清理请求有独立统计名称。
阶段不自动递增，使用 Locust 的 --users/--spawn-rate/--run-time 单独运行。
"""

from __future__ import annotations

import os
import random

from locust import HttpUser, between, task
from locust.exception import StopUser

from support import (
    REQUEST_GATE,
    add_safety_options,
    check_envelope,
    configure_http_auth,
    ensure_test_allowed,
    install_safety_monitor,
    response_json,
)


def _wait_bounds() -> tuple[float, float]:
    minimum = float(os.getenv("LT_WAIT_MIN", "0.1"))
    maximum = float(os.getenv("LT_WAIT_MAX", "0.5"))
    if minimum < 0 or maximum < minimum:
        raise ValueError("LT_WAIT_MIN/LT_WAIT_MAX 必须满足 0 <= min <= max")
    return minimum, maximum


def _weight(name: str, default: int) -> int:
    value = int(os.getenv(f"LT_WEIGHT_{name.upper()}", str(default)))
    if value < 0:
        raise ValueError(f"LT_WEIGHT_{name.upper()} 不能小于 0")
    return value


WAIT_MIN, WAIT_MAX = _wait_bounds()
WEIGHTS = {
    "list": _weight("list", 5),
    "detail": _weight("detail", 4),
    "skills": _weight("skills", 1),
    "health": _weight("health", 1),
}
if not any(WEIGHTS.values()):
    raise ValueError("至少需要一个非零 LT_WEIGHT_*")

TOKEN = os.getenv("LT_ADMIN_TOKEN", os.getenv("LT_TOKEN", "")).strip()
CONNECT_TIMEOUT = float(os.getenv("LT_CONNECT_TIMEOUT", "5"))
READ_TIMEOUT = float(os.getenv("LT_READ_TIMEOUT", "15"))

add_safety_options("basic", ("basic", "business"), default_max_rps=0.0)
install_safety_monitor()


def _contract_error(path: str, data: object, conversation_id: str = "") -> str | None:
    """验证成功信封中的关键字段，避免把空壳 HTTP 200 记成业务成功。"""
    if path == "/api/v1/system/health":
        return None if isinstance(data, dict) and data.get("status") == "ok" else "E_CONTRACT|health.status"
    if path == "/api/v1/skills":
        return None if isinstance(data, dict) and isinstance(data.get("skills"), list) else "E_CONTRACT|skills"
    if path == "/api/v1/conversation/list":
        return (
            None
            if isinstance(data, dict) and isinstance(data.get("conversations"), list)
            else "E_CONTRACT|conversation.list"
        )
    if path == "/api/v1/conversation/detail":
        return (
            None
            if isinstance(data, dict) and data.get("conversation_id") == conversation_id
            else "E_CONTRACT|conversation.detail"
        )
    if path == "/api/v1/conversation/create":
        return (
            None
            if isinstance(data, dict) and isinstance(data.get("conversation_id"), str) and data["conversation_id"]
            else "E_CONTRACT|conversation.create"
        )
    return None


class DataUser(HttpUser):
    wait_time = between(WAIT_MIN, WAIT_MAX)

    def on_start(self) -> None:
        ensure_test_allowed(self.environment)
        self.client.trust_env = False
        self.conversation_id = ""
        configure_http_auth(self.client)
        if TOKEN:
            self.client.headers.update({"X-Admin-Token": TOKEN})
        self._create_conversation()

    def _create_conversation(self) -> None:
        REQUEST_GATE.wait()
        with self.client.post(
            "/api/v1/conversation/create",
            json={"title": "lt-session", "mode": "memory"},
            name="[setup] POST /conversation/create",
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            catch_response=True,
        ) as response:
            body = response_json(response)
            reason = check_envelope(response, body)
            data = body.get("data") if body else None
            if reason is None:
                reason = _contract_error("/api/v1/conversation/create", data)
            if reason is not None:
                response.failure(reason)
                raise StopUser()
            self.conversation_id = data["conversation_id"]

    def _request(
        self,
        method: str,
        path: str,
        name: str,
        *,
        params: dict[str, object] | None = None,
        json_body: dict[str, object] | None = None,
        expected_path: str | None = None,
        conversation_id: str = "",
    ) -> dict | None:
        REQUEST_GATE.wait()
        with self.client.request(
            method,
            path,
            params=params,
            json=json_body,
            name=name,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            catch_response=True,
        ) as response:
            body = response_json(response)
            reason = check_envelope(response, body)
            if reason is None and expected_path:
                data = body.get("data") if body else None
                reason = _contract_error(expected_path, data, conversation_id)
            if reason is not None:
                response.failure(reason)
            return body

    def on_stop(self) -> None:
        if not self.conversation_id:
            return
        self._request(
            "POST",
            "/api/v1/conversation/delete",
            "[cleanup] POST /conversation/delete",
            json_body={"conversation_id": self.conversation_id},
        )
        self.conversation_id = ""

    def _list(self) -> None:
        self._request(
            "GET",
            "/api/v1/conversation/list",
            "GET /conversation/list",
            params={"limit": 50},
            expected_path="/api/v1/conversation/list",
        )

    def _detail(self) -> None:
        self._request(
            "GET",
            "/api/v1/conversation/detail",
            "GET /conversation/detail",
            params={"conversation_id": self.conversation_id},
            expected_path="/api/v1/conversation/detail",
            conversation_id=self.conversation_id,
        )

    def _skills(self) -> None:
        self._request(
            "GET",
            "/api/v1/skills",
            "GET /skills",
            expected_path="/api/v1/skills",
        )

    def _health(self) -> None:
        self._request(
            "GET",
            "/api/v1/system/health",
            "GET /system/health",
            expected_path="/api/v1/system/health",
        )

    @task
    def run_selected_scenario(self) -> None:
        scenario = self.environment.parsed_options.scenario
        if scenario == "business":
            # 每轮按真实业务顺序读取列表、会话详情及两个轻量静态接口。
            self._list()
            self._detail()
            self._skills()
            self._health()
            return

        actions = {
            "list": self._list,
            "detail": self._detail,
            "skills": self._skills,
            "health": self._health,
        }
        names = list(actions)
        action = random.choices(names, weights=[WEIGHTS[name] for name in names], k=1)[0]
        actions[action]()
