"""模型清单接口：按角色返回可选模型与默认模型。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.model_policy import allowed_models, default_model
from app.core.response import ok
from app.core.security import get_current_role

router = APIRouter(prefix="/api/v1", tags=["models"])


@router.get("/models")
def list_models(role: str = Depends(get_current_role)) -> dict:
    """未登录（视为普通用户）也开放：前端右上角选择器需要它渲染选项。"""
    return ok(
        {
            "role": role,
            "models": allowed_models(role),
            "default": default_model(role),
        }
    )
