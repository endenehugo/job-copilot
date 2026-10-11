"""统一响应信封：{code, message, data}。

与旧版保持同构：业务响应一律 HTTP 200，错误语义由 code 字段承载
（success / fail / not_found / unauthorized / forbidden / verified_error），
前端 axios 拦截器按 code !== "success" 统一抛错。

成功响应可额外携带 degraded / warnings 表达"结果产出但可信度受损"，
见 ok() 的说明。这两个字段只在降级时出现，向前兼容。
"""

from __future__ import annotations

from typing import Any


def ok(
    data: Any = None,
    message: str = "",
    *,
    degraded: bool = False,
    warnings: list[str] | None = None,
) -> dict:
    """成功响应。

    降级标记：当结果虽然产出但可信度受损（LLM 验证没跑成功、模型漏返回关键字段、
    重排降级等）时，用 degraded/warnings 告知调用方。

    只在真的降级时附加这两个字段——正常响应保持干净，也避免前端提示轰炸。
    前端拦截器看到 degraded 会统一弹提示，无需各组件自行处理。
    """
    body = {
        "code": "success",
        "message": message,
        "data": data if data is not None else {},
    }
    kept = [str(w).strip() for w in (warnings or []) if w]
    if degraded or kept:
        body["degraded"] = True
        body["warnings"] = kept
    return body


def fail(message: str = "", data: Any = None, code: str = "fail") -> dict:
    return {
        "code": code,
        "message": message,
        "data": data if data is not None else {},
    }


def message(code: str, msg: str = "") -> dict:
    return {"code": code, "message": msg, "data": {}}
