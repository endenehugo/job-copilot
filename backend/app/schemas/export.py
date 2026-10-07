"""导出相关请求模型。"""

from __future__ import annotations

from pydantic import BaseModel


class ExportProjectRewriteRequest(BaseModel):
    result: dict = {}
