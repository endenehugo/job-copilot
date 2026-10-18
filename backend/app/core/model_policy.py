"""角色模型白名单。

产品约定（2026-10-18）：
- 普通用户（未登录/登录态无效一律视为普通用户）默认 qwen-flash，可换 qwen-plus；
- 管理员可选 settings.llm_selectable_models 里的全部模型，默认取 settings.llm_model。

白名单在服务端强制校验（resolve_model），不信任前端传参；
降级链（llm_fallback_models）不受白名单限制——它只在额度类错误时兜底，
否则普通用户的 qwen-flash 额度耗尽后将直接报错。
"""

from __future__ import annotations

from app.core.config import settings

ROLE_USER = "user"
ROLE_ADMIN = "admin"

# 普通用户可选清单与默认模型（刻意不放进 Settings：这是产品决策而非环境配置）
USER_SELECTABLE_MODELS: list[str] = ["qwen-flash", "qwen-plus"]
USER_DEFAULT_MODEL = "qwen-flash"


def allowed_models(role: str) -> list[str]:
    """当前角色可选的模型清单（返回副本，避免调用方改坏全局状态）。"""
    if role == ROLE_ADMIN:
        return list(settings.llm_selectable_models)
    return list(USER_SELECTABLE_MODELS)


def default_model(role: str) -> str:
    """当前角色的默认模型。"""
    if role == ROLE_ADMIN:
        return settings.llm_model
    return USER_DEFAULT_MODEL


def resolve_model(role: str, requested: str | None) -> str:
    """把请求里的 model 字段解析为最终使用的模型。

    - 空/缺省 → 角色默认模型；
    - 不在角色白名单内 → ValueError（API 层转 400）。
    """
    model = (requested or "").strip()
    if not model:
        return default_model(role)
    if model not in allowed_models(role):
        raise ValueError(f"模型 {model} 不在当前角色的可选清单内")
    return model
