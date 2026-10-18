"""模拟面试相关请求模型。"""

from __future__ import annotations

from pydantic import BaseModel


class InterviewStartRequest(BaseModel):
    conversation_id: str = ""
    direction: str = "general"
    jd_text: str = ""
    # 空串 = 服务端按角色取默认值；越权模型由服务端 400
    model: str = ""


class InterviewAnswerRequest(BaseModel):
    session_id: str = ""
    answer: str = ""
    question_index: int = 0
    model: str = ""


class InterviewDeleteRequest(BaseModel):
    session_id: str = ""
