"""认证原语：密码哈希（bcrypt）与管理员 JWT（PyJWT）。

依赖环境已内置的 bcrypt 3.2.0 与 PyJWT 2.8.0，不新增第三方包。

角色判定契约：请求不带 X-Admin-Token、token 无效/过期/伪造，
一律视为普通用户（role="user"）——不抛 401，普通功能本就无需登录。
"""

from __future__ import annotations

import logging
import secrets
import time

import bcrypt
import jwt
from fastapi import Depends, Header, HTTPException

from app.core.config import settings

logger = logging.getLogger(__name__)

# AUTH_SECRET 缺失时的进程内兜底密钥：同一进程内签发/校验自洽，
# 重启后旧 token 全部失效（有启动告警，属预期行为而非静默失败）。
_fallback_secret: str | None = None


def _get_secret() -> str:
    global _fallback_secret
    if settings.auth_secret:
        return settings.auth_secret
    if _fallback_secret is None:
        _fallback_secret = secrets.token_hex(32)
        logger.warning("未配置 AUTH_SECRET，使用进程内随机密钥：重启后所有登录态将失效")
    return _fallback_secret


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), (password_hash or "").encode("utf-8"))
    except ValueError:
        # 库里存的不是合法 bcrypt 串（如手工改坏），按校验失败处理而非 500
        return False


def create_token(username: str, expire_hours: int | None = None) -> str:
    hours = expire_hours if expire_hours is not None else settings.auth_token_expire_hours
    now = int(time.time())
    payload = {
        "sub": username,
        "role": "admin",
        "iat": now,
        "exp": now + hours * 3600,
    }
    return jwt.encode(payload, _get_secret(), algorithm="HS256")


def decode_token(token: str) -> dict | None:
    """无效/过期返回 None，不抛异常——调用方只关心"是不是管理员"。"""
    if not token:
        return None
    try:
        return jwt.decode(token.strip(), _get_secret(), algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        logger.info("管理员 token 校验失败: %s", exc)
        return None


_VISITOR_AUDIENCE = "job-copilot-visitor"


def create_visitor_token(visitor_id: str) -> str:
    """签发给匿名访客的长期令牌。

    与管理员 token 共用密钥，但用 `aud` 区分用途：管理员 token 没有这个
    audience，所以拿它当 cookie 用会被 decode_visitor 拒掉；反过来，
    访客 token 缺 `role=admin`，也过不了 get_current_role。两套身份不互通。
    """
    return jwt.encode(
        {
            "sub": visitor_id,
            "aud": _VISITOR_AUDIENCE,
            "iat": int(time.time()),
        },
        _get_secret(),
        algorithm="HS256",
    )


def decode_visitor(token: str | None) -> str | None:
    """校验访客令牌，无效/过期/伪造一律返回 None。

    这里不抛异常也不发日志告警：访客 cookie 失效是常态（清缓存、换浏览器、
    密钥轮换），每次访问都记一条日志只会淹没真正的问题。
    """
    if not token:
        return None
    try:
        payload = jwt.decode(
            token.strip(),
            _get_secret(),
            algorithms=["HS256"],
            audience=_VISITOR_AUDIENCE,
        )
    except jwt.PyJWTError:
        return None
    visitor_id = str(payload.get("sub") or "")
    # 形态校验：签发侧固定 v_ + 24 hex，这里挡住手写 cookie 塞任意字符串
    if not visitor_id.startswith("v_") or len(visitor_id) > 64:
        return None
    return visitor_id


def get_current_role(x_admin_token: str = Header(default="", alias="X-Admin-Token")) -> str:
    """FastAPI 依赖：无 token / token 无效一律返回普通用户角色。"""
    payload = decode_token(x_admin_token)
    if payload and payload.get("role") == "admin":
        return "admin"
    return "user"


def require_admin(role: str = Depends(get_current_role)) -> str:
    """FastAPI 依赖：仅管理员可访问的管理接口用。"""
    if role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return role
