"""会话相关请求模型。"""

from __future__ import annotations

from pydantic import BaseModel


class CreateConversationRequest(BaseModel):
    title: str = "新对话"
    mode: str = "agent"


class ChatRequest(BaseModel):
    conversation_id: str = ""
    query: str = ""
    mode: str = "agent"
    image_urls: list[str] = []
