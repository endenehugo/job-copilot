"""访客身份中间件：第一次请求时懒签发签名 Cookie。

为什么是纯 ASGI 中间件而不是 `BaseHTTPMiddleware`：
后者会把 `StreamingResponse` 包一层 `_StreamingResponse`，历史上出现过响应头被
重建、以及 SSE 首帧被缓冲的问题。这里只需要在 `http.response.start` 消息上补一个
header，用纯 ASGI 直接改 `send` 消息最直白，也不介入 body 的迭代。

为什么懒签发而不做"前端启动先调一次 /auth/visitor"：
多一个往返就多一个"身份还没到手就调了接口"的空窗期。这个项目所有页面第一次
请求都会落到 `/api` 上，顺手签是最省事的。

为什么不校验 Origin / Referer：
本站是同源部署，`SameSite=Lax` 已经挡住跨站发起的请求携带 cookie；
再把 Origin 白名单写死在代码里，反而会让本地开发（5174 → 8018 跨端口）失效。
"""

from __future__ import annotations

import secrets
from http.cookies import SimpleCookie

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.ownership import VISITOR_COOKIE_MAX_AGE, VISITOR_COOKIE_NAME, VISITOR_PREFIX
from app.core.security import create_visitor_token, decode_visitor


class VisitorMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        cookie_header = Headers(scope=scope).get("cookie", "")
        visitor_id = decode_visitor(_read_cookie(cookie_header))

        if visitor_id:
            self._bind(scope, visitor_id)
            await self.app(scope, receive, send)
            return

        # 没有 cookie、或被篡改过：发一个新的。篡改不报错而是换身份，
        # 因为访客本来就没有"需要保护到报错的既有身份"
        visitor_id = f"{VISITOR_PREFIX}{secrets.token_hex(12)}"
        self._bind(scope, visitor_id)
        cookie_value = (
            f"{VISITOR_COOKIE_NAME}={create_visitor_token(visitor_id)}; "
            f"Path=/; Max-Age={VISITOR_COOKIE_MAX_AGE}; HttpOnly; SameSite=Lax"
        )

        async def send_with_cookie(message: Message) -> None:
            if message["type"] == "http.response.start":
                # 用 MutableHeaders 而不是自己拼 bytes：它负责按 latin-1 编码，
                # 也保证不破坏已有的 Set-Cookie（如果有）
                MutableHeaders(scope=message).append("set-cookie", cookie_value)
            await send(message)

        await self.app(scope, receive, send_with_cookie)

    @staticmethod
    def _bind(scope: Scope, visitor_id: str) -> None:
        """写进 scope["state"]，request.state.owner_key 即可读到。

        Starlette 的 Request.state 正是基于 scope["state"]，所以这里不需要
        自造一个 Request 对象。管理员身份不在这里判断——中间件拿不到
        X-Admin-Token 的语义优先级，覆盖留给 get_owner_key 依赖做。
        """
        state = scope.setdefault("state", {})
        state["owner_key"] = visitor_id


def _read_cookie(cookie_header: str) -> str | None:
    """从 Cookie 头里取访客令牌。

    不用 `Headers.get("cookie")` 直接切分：SimpleCookie 能正确处理引号与
    同名 cookie 的多种形态，出错时返回空而不是抛。
    """
    if not cookie_header:
        return None
    jar = SimpleCookie()
    try:
        jar.load(cookie_header)
    except Exception:  # 畸形 Cookie 头不该让整个请求 500
        return None
    morsel = jar.get(VISITOR_COOKIE_NAME)
    return morsel.value if morsel else None


__all__ = ["VisitorMiddleware"]
