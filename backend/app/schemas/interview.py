"""模拟面试相关请求模型。"""

from __future__ import annotations

from pydantic import BaseModel


class InterviewStartRequest(BaseModel):
    conversation_id: str = ""
    direction: str = "general"
    jd_text: str = ""


class InterviewAnswerRequest(BaseModel):
    session_id: str = ""
    answer: str = ""
    question_index: int = 0
