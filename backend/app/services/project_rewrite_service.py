from __future__ import annotations

import logging
from dataclasses import dataclass

from langchain_community.chat_models import ChatTongyi
from langchain_core.messages import HumanMessage, SystemMessage

from app.core.config import settings
from app.core.json_utils import parse_llm_json

logger = logging.getLogger(__name__)

PROJECT_REWRITE_SYSTEM_PROMPT = """你是一位资深的简历优化专家，擅长帮助候选人改写简历中的项目经历描述。
请严格分析用户提供的项目描述，并返回 **严格的 JSON**，包含以下字段：

- `original_issues` (list[str]): 原描述存在的问题列表（如缺乏量化、表达模糊、重点不突出等）
- `improved_version` (str): 优化后的通用版本，保留原意，但表达更清晰、结构化、有影响力
- `python_backend_version` (str): 面向 Python 后端实习/校招的优化版本，突出后端技术栈（Flask/FastAPI、MySQL、Redis、Docker 等）、系统设计、性能优化等
- `agent_version` (str): 面向 Agent / AI 应用实习/校招的优化版本，突出 LLM 应用、RAG、工具调用、Agent 编排、Prompt Engineering 等

优化原则：
1. 使用 STAR 原则（情境-任务-行动-结果）组织描述
2. 尽可能加入量化指标（QPS、响应时间、准确率、覆盖量等）
3. 突出个人贡献和技术深度，而非团队成果
4. 使用强动词（主导、设计、优化、搭建、实现等）
5. 每段控制在 3-5 行，中文字数不超过 200 字

请确保：
1. 输出的内容必须是 **纯 JSON 对象**，不要包含 markdown 代码块标记（```json）、不要额外解释。
2. 原描述问题（original_issues）至少 2 条，不超过 5 条。
3. 三个版本都要根据具体的项目内容来做针对性优化，不要输出通用模板。
"""


@dataclass
class ProjectRewriteService:
    _llm: ChatTongyi | None = None

    def rewrite(self, project_description: str, context: str | None = None) -> dict:
        if not project_description or not project_description.strip():
            raise ValueError("项目描述不能为空")

        content = self._invoke_llm(project_description.strip(), context)
        return self._parse_response(content)

    def _ensure_llm(self) -> ChatTongyi:
        if self._llm is None:
            self._llm = ChatTongyi(
                model=settings.llm_model,
                temperature=0.4,
                top_p=0.8,
            )
        return self._llm

    def _invoke_llm(self, project_description: str, context: str | None) -> str:
        llm = self._ensure_llm()

        user_prompt = f"请优化以下项目描述：\n\n{project_description}\n"
        if context:
            user_prompt += f"\n## 参考背景\n{context}\n"

        messages = [
            SystemMessage(content=PROJECT_REWRITE_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ]
        response = llm.invoke(messages)
        return response.content or ""

    def _parse_response(self, content: str) -> dict:
        logger.info("LLM project rewrite raw response (first 500 chars): %s", content[:500])
        result = parse_llm_json(content, "项目优化返回格式异常，无法提取结构化结果")
        return self._validate_and_normalize(result)

    @staticmethod
    def _validate_and_normalize(result: dict) -> dict:
        normalized = {
            "original_issues": result.get("original_issues", []) or [],
            "improved_version": str(result.get("improved_version", "") or "").strip(),
            "python_backend_version": str(result.get("python_backend_version", "") or "").strip(),
            "agent_version": str(result.get("agent_version", "") or "").strip(),
        }

        normalized["original_issues"] = [
            str(item).strip() for item in normalized["original_issues"] if item
        ]

        return normalized

