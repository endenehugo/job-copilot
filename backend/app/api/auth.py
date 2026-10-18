"""管理员登录与身份查询接口。

契约：
- 登录成功返回 JWT token（7 天过期），前端存 localStorage 并在每个请求
  的 X-Admin-Token 头携带；
- GET /auth/me 不要求登录：无 token 一律返回 role=user，供前端判断
  右上角显示"管理员登录"还是"管理员"徽标；
- 登录失败保持 HTTP 200 + 信封 code（401/400/500），与全站错误信封一致。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from app.core.config import settings
from app.core.response import fail, ok
from app.core.security import create_token, get_current_role, hash_password, verify_password
from app.repositories import AdminUserRepository, DatabaseManager
from app.schemas.auth import LoginRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login")
def login(payload: LoginRequest) -> dict:
    username = payload.username.strip()
    password = payload.password
    if not username or not password:
        return fail("用户名和密码不能为空", code=400)

    session = DatabaseManager.get_session()
    try:
        user = AdminUserRepository().get_by_username(session, username)
        if user is None or not verify_password(password, user.password_hash):
            # 用户不存在与密码错误给同一句话，避免账号枚举
            return fail("用户名或密码错误", code=401)
        token = create_token(user.username)
        return ok(
            {"token": token, "role": "admin", "username": user.username},
            message="登录成功",
        )
    except Exception as exc:
        logger.exception("管理员登录异常: %s", exc)
        return fail("登录服务异常，请稍后重试", code=500)
    finally:
        DatabaseManager.remove_session()


@router.get("/me")
def me(role: str = Depends(get_current_role)) -> dict:
    return ok({"role": role})


def ensure_initial_admin() -> None:
    """启动引导：表为空且配置了 ADMIN_USERNAME/ADMIN_INIT_PASSWORD 时创建初始管理员。

    在 lifespan 里调用；数据库不可用只告警不阻断启动（登录时再实际读库）。
    """
    if not settings.admin_username or not settings.admin_init_password:
        if settings.env in {"dev", "test"}:
            logger.info("未配置 ADMIN_USERNAME/ADMIN_INIT_PASSWORD，跳过初始管理员创建")
        return
    session = DatabaseManager.get_session()
    try:
        repo = AdminUserRepository()
        if repo.count(session) > 0:
            return
        repo.create(session, settings.admin_username, hash_password(settings.admin_init_password))
        session.commit()
        logger.info("已创建初始管理员：%s", settings.admin_username)
    except Exception as exc:
        session.rollback()
        logger.warning("初始管理员创建失败（不影响启动）：%s", exc)
    finally:
        DatabaseManager.remove_session()
