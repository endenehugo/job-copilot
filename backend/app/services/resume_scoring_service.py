from __future__ import annotations

import logging
from dataclasses import dataclass

from langchain_community.chat_models import ChatTongyi
from langchain_core.messages import HumanMessage, SystemMessage

from app.core.config import settings
from app.core.json_utils import parse_llm_json

logger = logging.getLogger(__name__)

SCORING_SYSTEM_PROMPT = """你是一位资深的简历评估专家，负责根据职位描述（JD）对候选人简历进行结构化评分。
请严格分析提供的简历文本和 JD 原文，返回 **严格的 JSON**，包含以下字段：

- `total_score` (number): 总分，满分 100 分
- `dimensions` (object): 四个维度的评分，每个维度满分 100 分
  - `skill_match` (number): 技能匹配度 — 简历中的技术栈与 JD 要求的匹配程度
  - `project_relevance` (number): 项目相关性 — 简历项目的业务/领域与 JD 的关联程度
  - `expression_quality` (number): 表达质量 — 简历描述是否清晰、结构化、有数据支撑
  - `job_fitness` (number): 岗位适配度 — 整体来看候选人与该职位的契合程度
- `strengths` (list[str]): 候选人的核心优势（相对于该 JD）
- `gaps` (list[str]): 候选人的主要缺口/不足（相对于该 JD）
- `suggestions` (list[str]): 针对性的改进建议，按优先级排列

评分标准：
1. 技能匹配度：JD 列出的每项核心技术，简历中出现了得 20 分/项，有项目深度使用得 25 分/项
2. 项目相关性：项目领域与 JD 业务领域直接相关得 80-100，间接相关得 50-79，不相关得 0-49
3. 表达质量：有量化指标（数字、指标）得 70-100，仅有描述无量化得 40-69，结构混乱得 0-39
4. 岗位适配度：综合考量经验年限、技能组合、项目背景与 JD 的整体匹配

请确保：
1. 输出的内容必须是 **纯 JSON 对象**，不要包含 markdown 代码块标记（```json）、不要额外解释。
2. 每个字段即使为空也要按类型给出默认值（空数组/0）。
3. 评分要保持一致性，total_score 建议为四个维度分的加权平均（技能 30%、项目 25%、表达 15%、适配 30%）。
4. strengths、gaps、suggestions 每项至少 2-3 条，最多不超过 6 条。
"""


@dataclass
class ResumeScoringService:
    _llm: ChatTongyi | None = None

    def score(self, resume_text: str, jd_text: str, jd_analysis: dict | None = None) -> dict:
        if not resume_text or not resume_text.strip():
            raise ValueError("简历文本不能为空")
        if not jd_text or not jd_text.strip():
            raise ValueError("JD 文本不能为空")

        content = self._invoke_llm(resume_text.strip(), jd_text.strip(), jd_analysis)
        return self._parse_response(content)

    def _ensure_llm(self) -> ChatTongyi:
        if self._llm is None:
            self._llm = ChatTongyi(
                model=settings.llm_model,
                temperature=0.3,
                top_p=0.7,
            )
        return self._llm

    def _invoke_llm(self, resume_text: str, jd_text: str, jd_analysis: dict | None) -> str:
        llm = self._ensure_llm()

        jd_context = f"## JD 原文\n\n{jd_text}\n"
        if jd_analysis:
            jd_context += (
                f"\n## JD 解析结果\n"
                f"- 岗位名称：{jd_analysis.get('job_role', '')}\n"
                f"- 核心关键词：{', '.join(jd_analysis.get('keywords', []) or [])}\n"
                f"- 硬性要求：{'; '.join(jd_analysis.get('requirements', []) or [])}\n"
                f"- 加分项：{'; '.join(jd_analysis.get('bonus_points', []) or [])}\n"
            )

        user_prompt = f"{jd_context}\n## 简历原文\n\n{resume_text}\n"
        messages = [
            SystemMessage(content=SCORING_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ]
        response = llm.invoke(messages)
        return response.content or ""

    def _parse_response(self, content: str) -> dict:
        logger.info("LLM scoring raw response (first 500 chars): %s", content[:500])
        result = parse_llm_json(content, "评分返回格式异常，无法提取结构化结果")
        return self._validate_and_normalize(result)

    @staticmethod
    def _validate_and_normalize(result: dict) -> dict:
        dimensions = result.get("dimensions", {}) or {}
        normalized = {
            "total_score": max(0, min(100, int(float(result.get("total_score", 0) or 0)))),
            "dimensions": {
                "skill_match": max(0, min(100, int(float(dimensions.get("skill_match", 0) or 0)))),
                "project_relevance": max(0, min(100, int(float(dimensions.get("project_relevance", 0) or 0)))),
                "expression_quality": max(0, min(100, int(float(dimensions.get("expression_quality", 0) or 0)))),
                "job_fitness": max(0, min(100, int(float(dimensions.get("job_fitness", 0) or 0)))),
            },
            "strengths": [str(s).strip() for s in (result.get("strengths", []) or []) if s],
            "gaps": [str(g).strip() for g in (result.get("gaps", []) or []) if g],
            "suggestions": [str(s).strip() for s in (result.get("suggestions", []) or []) if s],
        }

        return normalized

