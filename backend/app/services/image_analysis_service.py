from __future__ import annotations

import logging
from dataclasses import dataclass

from langchain_community.chat_models import ChatTongyi
from langchain_core.messages import HumanMessage, SystemMessage

from app.core.config import settings

logger = logging.getLogger(__name__)

SCREENSHOT_JD_SYSTEM_PROMPT = """你是一个专业的招聘信息识别专家。
用户会上传一张招聘截图（来自招聘网站、BOSS 直聘、猎聘、拉勾等平台的职位详情截图）。
请从截图中提取完整的职位描述（JD）文本，包括：

1. 岗位名称 / 职位名称
2. 岗位职责 / 工作内容
3. 任职要求 / 资格条件
4. 技术栈要求（编程语言、框架、中间件等）
5. 学历和经验要求
6. 加分项 / 优先条件
7. 薪资范围（如果有）

要求：
- 尽可能完整地提取所有文本信息
- 保持原文的关键词和专业术语
- 如果图片中的文字模糊或无法识别，请如实说明
- 输出格式：请直接输出提取到的 JD 文本，不要加额外解释
- 用 Markdown 格式组织内容，方便后续解析
"""


@dataclass
class ImageAnalysisService:
    _vl_llm: ChatTongyi | None = None

    def extract_jd_from_screenshot(self, image_path: str) -> str:
        """使用多模态模型从招聘截图中提取 JD 文本"""
        if not image_path:
            raise ValueError("图片路径不能为空")

        llm = self._ensure_vl_llm()
        content_parts = [
            {"image": image_path},
            {"text": "请从这张招聘截图中提取完整的职位描述（JD）文本。"},
        ]

        messages = [
            SystemMessage(content=SCREENSHOT_JD_SYSTEM_PROMPT),
            HumanMessage(content=content_parts),
        ]

        response = llm.invoke(messages)
        return self._extract_text(response.content) or ""

    def _ensure_vl_llm(self) -> ChatTongyi:
        if self._vl_llm is None:
            self._vl_llm = ChatTongyi(
                model=settings.multimodal_model,
                temperature=0.3,
                top_p=0.7,
            )
        return self._vl_llm

    @staticmethod
    def _extract_text(content) -> str:
        """从多模态响应中提取纯文本"""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(str(item.get("text", "") or ""))
                elif isinstance(item, str):
                    parts.append(item)
            return "".join(parts)
        return str(content)
