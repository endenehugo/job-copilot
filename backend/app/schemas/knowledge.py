"""知识库相关请求模型。"""

from __future__ import annotations

from pydantic import BaseModel


class KnowledgeSubmitRequest(BaseModel):
    content: str = ""


class SelfExpandRequest(BaseModel):
    count: int = 3
