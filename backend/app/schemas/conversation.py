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
    # 本次请求使用的模型；空串 = 服务端按角色取默认值，越权模型由服务端 400
    model: str = ""


class DeleteConversationRequest(BaseModel):
    conversation_id: str = ""


class PinConversationRequest(BaseModel):
    conversation_id: str = ""
    # 默认置顶，取消置顶显式传 false
    pinned: bool = True
