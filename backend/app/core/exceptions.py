"""全局异常处理：所有业务异常都转成统一信封返回。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.response import fail

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """业务异常：service 层主动抛出，携带用户可读的 message。

    code 沿用旧版信封语义：错误时为 HTTP 语义整数（400/500），
    HTTP 状态保持 200，由前端按 code !== "success" 判错。
    """

    def __init__(self, message: str = "", code: int = 400, data: Any = None):
        super().__init__(message)
        self.message = message or "请求处理失败"
        self.code = code
        self.data = data


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError):
        return JSONResponse(status_code=200, content=fail(exc.message, exc.data, exc.code))

    @app.exception_handler(ValueError)
    async def _handle_value_error(request: Request, exc: ValueError):
        # service 层大量使用 ValueError 携带用户可读的中文错误信息
        return JSONResponse(status_code=200, content=fail(str(exc) or "请求参数不合法", code=400))

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        first = errors[0] if errors else {}
        location = ".".join(str(item) for item in first.get("loc", []))
        msg = f"参数校验失败：{location} {first.get('msg', '')}".strip()
        return JSONResponse(status_code=200, content=fail(msg, code=400))

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception):
        logger.exception("未处理异常: %s", exc)
        return JSONResponse(status_code=500, content=fail("服务器内部错误", code=500))
