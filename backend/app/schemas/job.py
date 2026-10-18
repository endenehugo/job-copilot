"""求职业务相关请求模型。"""

from __future__ import annotations

from pydantic import BaseModel


class JobAnalyzeRequest(BaseModel):
    conversation_id: str = ""
    jd_text: str = ""
    # 空串 = 服务端按角色取默认值；越权模型由服务端 400
    model: str = ""


class ProjectRewriteRequest(BaseModel):
    conversation_id: str = ""
    project_description: str = ""
    model: str = ""


class JobAnalysisDeleteRequest(BaseModel):
    analysis_id: int = 0


class ProjectRewriteDeleteRequest(BaseModel):
    rewrite_id: int = 0


class ScreenshotAnalyzeRequest(BaseModel):
    conversation_id: str = ""
    image_url: str = ""
    model: str = ""


class JobAnalyzeStreamRequest(BaseModel):
    """流式 JD 分析：文本与截图二选一（都有时按截图处理）。"""

    conversation_id: str = ""
    jd_text: str = ""
    image_url: str = ""
    model: str = ""
