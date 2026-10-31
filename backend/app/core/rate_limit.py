"""轻量限流中间件：给公网上的昂贵接口兜底。

为什么自己写而不用 slowapi：需要的只是"按身份 + 按时间窗计数"，纯 ASGI
中间件几十行；引入 slowapi 意味着再拉一个依赖、再学一套装饰器语义，与
visitor_middleware 同款的自研实现反而更贴本项目"进程内单 worker"的部署形态。

三个桶（全部可经 .env 放宽，0 = 关闭该桶）：
- ai：所有会烧 LLM 额度的 POST（chat / JD 分析 / 面试 / 简历生成与改写 /
  知识自扩）——配额熔断（llm_usage）管"今天总共烧多少"，这里管"烧多快"，
  两者是互补的：熔断挡脚本囤积，限流挡突发打满。
- upload：文件/图片上传（解析 + embedding 都有成本）。
- login：按 IP 计数的登录失败面——没有它，登录接口可以无限暴力试密码。

身份取值不依赖 VisitorMiddleware 的执行顺序（中间件注册顺序在内外层之间
是脆弱的），直接自己解 cookie / X-Admin-Token：管理员豁免，其次访客 id，
都没有就退回客户端 IP。拒绝时回真实 HTTP 429 + 统一信封 JSON，
axios 拦截器与 SSE 前端都能按 message 展示。

滑动窗口用 deque 存时间戳：窗口秒级精度、内存每键 O(窗口内请求数)，
对单 worker 的演示站绰绰有余。定期清扫防止键集合只增不减。
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings
from app.core.response import fail
from app.core.security import decode_token, decode_visitor
from app.core.visitor_middleware import _read_cookie

# (桶名, 方法, 路径前缀)。方法参与匹配：AI 桶只数 POST，
# 这样 GET 列表/详情（如 /resume/generations/{id}）不会误伤。
# 前缀匹配是有意的：/conversation/chat 前缀同时盖住 /chat/stream，
# /job/analyze 同时盖住 /analyze/stream 与 /analyze-from-screenshot。
_RULES: tuple[tuple[str, str, str], ...] = (
    ("login", "POST", "/api/v1/auth/login"),
    ("upload", "POST", "/api/v1/document/upload"),
    ("upload", "POST", "/api/v1/conversation/image/upload"),
    ("ai", "POST", "/api/v1/conversation/chat"),
    ("ai", "POST", "/api/v1/job/analyze"),
    ("ai", "POST", "/api/v1/interview/start"),
    ("ai", "POST", "/api/v1/interview/answer"),
    ("ai", "POST", "/api/v1/resume/generate"),
    ("ai", "POST", "/api/v1/resume/generations/"),
    ("ai", "POST", "/api/v1/resume/project/rewrite"),
    ("ai", "POST", "/api/v1/knowledge/self-expand"),
    ("ai", "POST", "/api/v1/knowledge/rebuild"),
)


@dataclass
class _Window:
    """一个滑动窗口：窗口秒数 + 时间戳队列。"""

    seconds: int
    stamps: deque = field(default_factory=deque)

    def hit_and_check(self, limit: int, now: float) -> bool:
        """登记一次并返回是否放行。过期时间戳先出队。"""
        while self.stamps and self.stamps[0] <= now - self.seconds:
            self.stamps.popleft()
        if len(self.stamps) >= limit:
            return False
        self.stamps.append(now)
        return True


def _buckets_for(name: str) -> list[_Window]:
    if name == "ai":
        limits = [
            (settings.rate_limit_ai_per_minute, 60),
            (settings.rate_limit_ai_per_hour, 3600),
        ]
    elif name == "upload":
        limits = [(settings.rate_limit_upload_per_hour, 3600)]
    else:  # login
        limits = [(settings.rate_limit_login_per_10min, 600)]
    return [_Window(seconds=window) for _, window in limits]


class RateLimitMiddleware:
    _SWEEP_INTERVAL = 300  # 全表清扫周期（秒）

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._hits: dict[tuple[str, str, int], _Window] = {}
        self._lock = threading.Lock()
        self._last_sweep = time.monotonic()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return
        if not settings.rate_limit_enabled:
            await self.app(scope, receive, send)
            return

        rule = self._match(scope)
        if rule is None:
            await self.app(scope, receive, send)
            return
        bucket_name, _method, _prefix = rule
        windows = _buckets_for(bucket_name)
        if not windows:
            await self.app(scope, receive, send)
            return

        identity = self._identity(scope)
        if identity == "admin":
            # 管理员豁免：自己压测/演示自己的产品不该被自己挡住
            await self.app(scope, receive, send)
            return
        allowed = True
        retry_after = 0
        now = time.monotonic()
        with self._lock:
            self._maybe_sweep(now)
            for index, window in enumerate(windows):
                key = (bucket_name, identity, index)
                slot = self._hits.setdefault(key, window)
                limit = self._limit(bucket_name, index)
                if limit <= 0:  # 该窗口显式关闭
                    continue
                if not slot.hit_and_check(limit, now):
                    allowed = False
                    retry_after = max(retry_after, int(window.seconds))
        if allowed:
            await self.app(scope, receive, send)
            return
        await self._reject(send, bucket_name, retry_after)

    # ------------------------------------------------------------------
    def _match(self, scope: Scope) -> tuple[str, str, str] | None:
        path = scope.get("path", "")
        method = scope.get("method", "")
        for rule in _RULES:
            name, rule_method, prefix = rule
            if method == rule_method and path.startswith(prefix):
                return rule
        return None

    @staticmethod
    def _limit(bucket_name: str, index: int) -> int:
        if bucket_name == "ai":
            limits = (settings.rate_limit_ai_per_minute, settings.rate_limit_ai_per_hour)
        elif bucket_name == "upload":
            limits = (settings.rate_limit_upload_per_hour,)
        else:
            limits = (settings.rate_limit_login_per_10min,)
        return limits[index] if index < len(limits) else 0

    @staticmethod
    def _identity(scope: Scope) -> str:
        """管理员豁免 > 访客 id > 客户端 IP。自己解 cookie，不依赖中间件顺序。"""
        headers = Headers(scope=scope)
        if decode_token(headers.get("x-admin-token", "")):
            return "admin"
        visitor = decode_visitor(_read_cookie(headers.get("cookie", "")))
        if visitor:
            return visitor
        client = scope.get("client")
        return "ip:" + (client[0] if client else "unknown")

    def _maybe_sweep(self, now: float) -> None:
        """锁内调用。清掉完全过期的窗口，防止键集合随历史访客只增不减。"""
        if now - self._last_sweep < self._SWEEP_INTERVAL:
            return
        self._last_sweep = now
        expired = [
            key
            for key, window in self._hits.items()
            if not window.stamps or window.stamps[-1] <= now - window.seconds
        ]
        for key in expired:
            self._hits.pop(key, None)

    @staticmethod
    async def _reject(send: Send, bucket_name: str, retry_after: int) -> None:
        messages = {
            "ai": "操作太频繁了，请休息一下再试",
            "upload": "上传太频繁了，请稍后再试",
            "login": "尝试次数过多，请 10 分钟后再试",
        }
        body = json.dumps(
            fail(messages.get(bucket_name, "操作太频繁，请稍后再试"), code=429),
            ensure_ascii=False,
        ).encode("utf-8")
        headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode()),
        ]
        if retry_after > 0:
            headers.append((b"retry-after", str(retry_after).encode()))
        await send({"type": "http.response.start", "status": 429, "headers": headers})
        await send({"type": "http.response.body", "body": body})


__all__ = ["RateLimitMiddleware"]
