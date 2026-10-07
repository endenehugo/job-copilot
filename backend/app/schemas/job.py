"""求职业务相关请求模型。"""

from __future__ import annotations

from pydantic import BaseModel


class JobAnalyzeRequest(BaseModel):
    conversation_id: str = ""
    jd_text: str = ""


class ProjectRewriteRequest(BaseModel):
    conversation_id: str = ""
    project_description: str = ""


class ScreenshotAnalyzeRequest(BaseModel):
    conversation_id: str = ""
    image_url: str = ""
