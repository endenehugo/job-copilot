"""系统接口：健康检查与 API Key 检测。"""

from __future__ import annotations

import time
from dataclasses import asdict

from fastapi import APIRouter, Request

from app.core.config import settings
from app.core.response import ok
from app.utils.api_key_checker import check_all

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get("/health")
def health(request: Request) -> dict:
    """进程健康检查，不依赖外部服务。

    keys_ok 来自启动时的一次性检测缓存（lifespan 中跑 check_all），
    这里不实时调外部 API，避免健康检查被网络拖慢；实时检测走 /keycheck。
    """
    keys_ok = getattr(request.app.state, "keys_ok", None)
    return ok({
        "status": "ok",
        "env": settings.env,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "keys_ok": keys_ok,
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
