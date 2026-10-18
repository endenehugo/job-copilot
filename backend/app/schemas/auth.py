"""认证相关请求模型。"""

from __future__ import annotations

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str = ""
    password: str = ""
