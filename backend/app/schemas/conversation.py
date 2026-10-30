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
    # 用户在输入框显式选择的技能名；空串 = 交给模型自行判断。
    # 非空时服务端在系统提示后插入该技能的指南，且不再让模型 load_skill。
    skill: str = ""
    # 产出范围（仅 project-guide 使用）：guide / interview / both，空串按 both。
    # 该技能的交付物是导学 + 面经两份文件，一次全出接近 1.9 万输出 token，
    # 由用户先选清楚能把单次生成量减半。其他技能忽略此字段。
    skill_option: str = ""


class DeleteConversationRequest(BaseModel):
    conversation_id: str = ""


class PinConversationRequest(BaseModel):
    conversation_id: str = ""
    # 默认置顶，取消置顶显式传 false
    pinned: bool = True
