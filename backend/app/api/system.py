"""系统接口：健康检查与 API Key 检测。"""

from __future__ import annotations

import time
from dataclasses import asdict

from fastapi import APIRouter, Request

from app.core.config import settings
from app.core.response import ok
from app.utils.api_key_checker import check_all

router = APIRouter(prefix="/api/v1/system", tags=["system"])


def _db_ok() -> bool:
    """MySQL 连通性探测：SELECT 1，任何异常都算不健康。

    原来 /health 有意不碰外部服务（快、不被网络拖慢），但编排层因此分不清
    "进程活着"和"服务可用"——DB 挂了健康检查照样绿，容器永远不会被重启。
    折中：探测放在 healthcheck 看得见的地方，SELECT 1 在本机内网是毫秒级；
    只读一行，不触碰业务表。
    """
    try:
        from sqlalchemy import text

        from app.repositories import DatabaseManager

        session = DatabaseManager.get_session()
        try:
            session.execute(text("SELECT 1"))
        finally:
            DatabaseManager.remove_session()
        return True
    except Exception:
        return False


@router.get("/health")
def health(request: Request) -> dict:
    """进程 + 依赖健康检查。

    keys_ok 来自启动时的一次性检测缓存（lifespan 中跑 check_all），
    这里不实时调外部 API，避免健康检查被网络拖慢；实时检测走 /keycheck。
    db 是实时探测（SELECT 1）：compose 的 healthcheck 靠它感知数据库故障。
    """
    keys_ok = getattr(request.app.state, "keys_ok", None)
    return ok({
        "status": "ok",
        "env": settings.env,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "keys_ok": keys_ok,
        "db": "ok" if _db_ok() else "fail",
    })


@router.get("/keycheck")
def keycheck() -> dict:
    """实时检测 DashScope Key 有效性及多模态权限。"""
    report = check_all()
    payload = {
        "all_passed": report.all_passed,
        "results": [asdict(result) for result in report.results],
    }
    message = "所有检测通过" if report.all_passed else "存在未通过项，请检查配置"
    return ok(payload, message)
