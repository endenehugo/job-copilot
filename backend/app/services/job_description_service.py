from __future__ import annotations

import logging
from dataclasses import dataclass

from langchain_community.chat_models import ChatTongyi
from langchain_core.messages import HumanMessage, SystemMessage

from app.core.config import settings
from app.core.json_utils import parse_llm_json

logger = logging.getLogger(__name__)

JD_ANALYSIS_SYSTEM_PROMPT = """你是一位资深的招聘分析专家，擅长分析招聘职位描述（JD）。
请严格分析用户提供的 JD 文本，并返回 **严格的 JSON**，包含以下字段：

- `job_role` (str): 岗位名称/职位名称
- `keywords` (list[str]): 从 JD 中提取的核心技术关键词列表（如 Python、Flask、MySQL、Redis、Docker 等）
- `requirements` (list[str]): 硬性要求列表（学历、经验年限、必须掌握的技术等）
- `bonus_points` (list[str]): 加分项列表（优先条件、加分技能等）
- `suggested_project_angles` (list[str]): 建议在简历项目中突出的角度（如何让自己的经历贴合该 JD 的侧重点）

请确保：
1. 输出的内容必须是 **纯 JSON 对象**，不要包含 markdown 代码块标记（```json）、不要额外解释。
2. 每个字段的值即使为空也要给出空数组。
3. keywords 要尽可能全面，包括编程语言、框架、中间件、工具等。
4. requirements 和 bonus_points 请基于 JD 原文精准归纳，不要凭空添加。
"""


@dataclass
class JobDescriptionService:
    _llm: ChatTongyi | None = None

    def analyze(self, jd_text: str) -> dict:
        if not jd_text or not jd_text.strip():
            raise ValueError("JD 文本不能为空")

        if len(jd_text.strip()) < 10:
            raise ValueError("JD 文本太短，请提供完整的职位描述")

        content = self._invoke_llm(jd_text.strip())
        return self._parse_response(content)

    def _ensure_llm(self) -> ChatTongyi:
        if self._llm is None:
            self._llm = ChatTongyi(
                model=settings.llm_model,
                temperature=0.3,
                top_p=0.7,
            )
        return self._llm

    def _invoke_llm(self, jd_text: str) -> str:
        llm = self._ensure_llm()
        messages = [
            SystemMessage(content=JD_ANALYSIS_SYSTEM_PROMPT),
            HumanMessage(content=f"请分析以下职位描述：\n\n{jd_text}"),
        ]
        response = llm.invoke(messages)
        return response.content or ""

    def _parse_response(self, content: str) -> dict:
        logger.info("LLM JD analysis raw response (first 500 chars): %s", content[:500])
        result = parse_llm_json(content, "JD 解析返回格式异常，无法提取结构化结果")
        return self._validate_and_normalize(result)

    @staticmethod
    def _validate_and_normalize(result: dict) -> dict:
        normalized = {
            "job_role": str(result.get("job_role", "")).strip(),
            "keywords": result.get("keywords", []) or [],
            "requirements": result.get("requirements", []) or [],
            "bonus_points": result.get("bonus_points", []) or [],
            "suggested_project_angles": result.get("suggested_project_angles", []) or [],
        }

        # 确保列表字段都是字符串列表
        for list_field in ("keywords", "requirements", "bonus_points", "suggested_project_angles"):
            normalized[list_field] = [str(item).strip() for item in normalized[list_field] if item]

        return normalized

