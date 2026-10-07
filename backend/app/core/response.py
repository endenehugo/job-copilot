"""统一响应信封：{code, message, data}。

与旧版保持同构：业务响应一律 HTTP 200，错误语义由 code 字段承载
（success / fail / not_found / unauthorized / forbidden / verified_error），
前端 axios 拦截器按 code !== "success" 统一抛错。
"""

from __future__ import annotations

from typing import Any


def ok(data: Any = None, message: str = "") -> dict:
    return {
        "code": "success",
        "message": message,
        "data": data if data is not None else {},
    }


def fail(message: str = "", data: Any = None, code: str = "fail") -> dict:
    return {
        "code": code,
        "message": message,
        "data": data if data is not None else {},
    }


def message(code: str, msg: str = "") -> dict:
    return {"code": code, "message": msg, "data": {}}
