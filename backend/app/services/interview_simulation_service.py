from __future__ import annotations

import logging
from dataclasses import dataclass

from langchain_community.chat_models import ChatTongyi
from langchain_core.messages import HumanMessage, SystemMessage

from app.core.config import settings
from app.core.json_utils import parse_llm_json

logger = logging.getLogger(__name__)

INTERVIEW_START_SYSTEM_PROMPT = """你是一位资深的面试官，擅长根据职位描述（JD）和候选人简历，出针对性的面试题。
请严格分析提供的 JD 和简历，生成一组面试题，返回 **严格的 JSON**，包含以下字段：

- `questions` (list[object]): 面试题列表，每题包含：
  - `question` (str): 面试题目
  - `expected_points` (list[str]): 参考答题要点
  - `difficulty` (str): 难度级别，可选 "easy" / "medium" / "hard"
  - `direction` (str): 题目方向，可选 "python_backend" / "agent_ai" / "general"

出题原则：
1. 题目应覆盖 JD 中提到的核心技术栈和业务领域
2. 结合候选人简历中的项目经验，提出个性化问题
3. 至少 3 题，不超过 6 题
4. 兼顾深度和广度，包含基础题和进阶题
5. Python 后端方向侧重：Flask/FastAPI、MySQL、Redis、Docker、Linux、系统设计
6. Agent/AI 方向侧重：LLM 原理、RAG、Agent 编排、Prompt Engineering、工具调用
7. 通用方向侧重：项目架构、问题排查、工程化、团队协作

请确保：
1. 输出的内容必须是 **纯 JSON 对象**，不要包含 markdown 代码块标记（```json）、不要额外解释。
2. 每个字段即使为空也要给出默认值。
3. 题目要具体，不要过于宽泛。
"""

INTERVIEW_ANSWER_SYSTEM_PROMPT = """你是一位资深的面试官，正在对候选人进行面试。
你需要根据之前的面试对话、当前题目和候选人的回答，给出评价并继续提问。

请严格分析，返回 **严格的 JSON**，包含以下字段：

- `evaluation` (str): 对候选人当前回答的评价（50-150 字），指出优点和不足
- `score` (int): 该回答的评分（满分 100 分）
- `next_action` (str): 下一步行动，可选 "continue" / "summary"
  - "continue": 继续提问，给出下一题或追问
  - "summary": 认为面试可以结束，给出整体总结
- `follow_up` (str | null): 如果 next_action 为 "continue"，这里是下一题或需要追问的内容；如果 next_action 为 "summary"，这里为 null
- `overall_summary` (str | null): 如果 next_action 为 "summary"，这里是整体面试总结（包括表现亮点、待加强点、综合建议）；否则为 null

评分标准：
1. 技术准确性（40 分）：概念理解是否正确，技术细节是否到位
2. 表达清晰度（20 分）：回答是否有条理、结构清晰
3. 深度与思考（40 分）：是否有深度思考，能否举一反三

请确保：
1. 输出的内容必须是 **纯 JSON 对象**，不要包含 markdown 代码块标记（```json）、不要额外解释。
2. 评分要客观公正，给建设性反馈。
3. 整场面试最多进行 8 轮（含初始问题），超过后 next_action 应设为 "summary"。
"""


@dataclass
class InterviewSimulationService:
    _start_llm: ChatTongyi | None = None
    _answer_llm: ChatTongyi | None = None

    def start_interview(self, jd_text: str, resume_text: str) -> dict:
        if not jd_text or not jd_text.strip():
            raise ValueError("JD 文本不能为空")
        if not resume_text or not resume_text.strip():
            raise ValueError("简历文本不能为空")

        content = self._invoke_start_llm(jd_text.strip(), resume_text.strip())
        return self._parse_response(content)

    def evaluate_answer(
        self,
        jd_text: str,
        resume_text: str,
        history: list[dict],
        current_question: str,
        user_answer: str,
        round_number: int,
    ) -> dict:
        if not user_answer or not user_answer.strip():
            raise ValueError("回答不能为空")

        content = self._invoke_answer_llm(
            jd_text, resume_text, history, current_question, user_answer, round_number
        )
        return self._parse_response(content)

    def _ensure_start_llm(self) -> ChatTongyi:
        if self._start_llm is None:
            self._start_llm = ChatTongyi(
                model=settings.llm_model,
                temperature=0.5,
                top_p=0.8,
            )
        return self._start_llm

    def _ensure_answer_llm(self) -> ChatTongyi:
        if self._answer_llm is None:
            self._answer_llm = ChatTongyi(
                model=settings.llm_model,
                temperature=0.4,
                top_p=0.7,
            )
        return self._answer_llm

    def _invoke_start_llm(self, jd_text: str, resume_text: str) -> str:
        llm = self._ensure_start_llm()
        user_prompt = (
            f"## JD 原文\n\n{jd_text}\n\n"
            f"## 简历原文\n\n{resume_text}\n\n"
            f"请根据以上 JD 和简历，生成针对性的面试题。"
        )
        messages = [
            SystemMessage(content=INTERVIEW_START_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ]
        response = llm.invoke(messages)
        return response.content or ""

    def _invoke_answer_llm(
        self,
        jd_text: str,
        resume_text: str,
        history: list[dict],
        current_question: str,
        user_answer: str,
        round_number: int,
    ) -> str:
        llm = self._ensure_answer_llm()

        history_text = ""
        for item in history:
            role = item.get("role", "")
            content = item.get("content", "")
            history_text += f"{'面试官' if role == 'assistant' else '候选人'}：{content}\n\n"

        user_prompt = (
            f"## JD 原文\n\n{jd_text}\n\n"
            f"## 简历原文\n\n{resume_text}\n\n"
            f"## 面试历史\n{history_text if history_text else '（无历史）'}\n"
            f"## 当前轮次\n第 {round_number} 轮\n\n"
            f"## 当前题目\n{current_question}\n\n"
            f"## 候选人回答\n{user_answer}\n"
        )
        messages = [
            SystemMessage(content=INTERVIEW_ANSWER_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ]
        response = llm.invoke(messages)
        return response.content or ""

    def _parse_response(self, content: str) -> dict:
        logger.info("LLM interview raw response (first 500 chars): %s", content[:500])
        return parse_llm_json(content, "面试服务返回格式异常，无法提取结构化结果")

