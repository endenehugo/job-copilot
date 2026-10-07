"""知识库内容的 AI 审核与自主生成。

审核（review）：判断提交内容是否属于"面试相关知识"，给出分类、标题与结论，
只有 related=true 且不重复的内容才会入库（fail-closed：审核服务不可用时不入库）。
自主扩充（generate_candidates）：由 AI 生成新的面试知识条目候选，
候选仍需走同一套 review 流程自审——生成者与审核者分离，避免自我放水。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from langchain_community.chat_models import ChatTongyi
from langchain_core.messages import HumanMessage, SystemMessage

from app.core.config import settings
from app.core.json_utils import parse_llm_json

logger = logging.getLogger(__name__)

# 知识分类枚举：前 5 类与内置知识库一致，后 4 类覆盖动态内容的常见主题
KNOWLEDGE_CATEGORIES = [
    "python_backend",
    "database",
    "linux",
    "flask_engineering",
    "agent_rag",
    "frontend",
    "algorithm",
    "behavioral",
    "general",
]

REVIEW_SYSTEM_PROMPT = """你是面试知识库的内容审核专家。请审核用户提交的内容能否收录进"面试知识库"。

收录标准（全部满足才 related=true）：
1. 与求职/面试强相关：技术面试题与八股、系统设计、工程实践常考知识点、面试技巧、简历与求职准备等；
2. 是可复用的知识，而不是一次性的闲聊、新闻、广告或与面试无关的技术文档；
3. 与已有条目主题不重复（duplicate=true 表示重复）。

已有条目标题：
{titles}

输出严格的 JSON 对象（不要 markdown 代码块标记、不要额外解释）：
{{"related": true/false, "duplicate": true/false, "category": "必须从 {categories} 中选一个", "title": "10字以内标题", "reason": "一句话审核结论"}}"""

GENERATE_SYSTEM_PROMPT = """你是一位资深面试官与面试知识库编辑。请围绕求职面试场景，生成 {count} 条全新的面试知识条目。

要求：
1. 主题必须与面试强相关：技术面试题/八股、系统设计、工程实践常考知识点、面试技巧、简历与求职准备；
2. 每条包含 title（10 字以内）、category（必须从 {categories} 中选一个）、content（120-300 字，按"面试会怎么问 + 标准答案要点"组织）；
3. 避开以下已有主题：{titles}
4. 输出严格的 JSON 对象：{{"entries": [{{"title": "...", "category": "...", "content": "..."}}]}}"""


@dataclass
class KnowledgeReviewService:
    _llm: ChatTongyi | None = None

    def review(self, content: str, existing_titles: list[str]) -> dict:
        """AI 审核一条内容：是否面试相关、是否重复、归类与标题建议。"""
        if not content or not content.strip():
            raise ValueError("内容不能为空")

        titles_text = "\n".join(f"- {t}" for t in existing_titles) or "（暂无）"
        system = REVIEW_SYSTEM_PROMPT.format(titles=titles_text, categories="、".join(KNOWLEDGE_CATEGORIES))
        user = f"待审核内容：\n{content.strip()[:4000]}"
        result = parse_llm_json(
            self._invoke_llm(system, user), "审核结果解析失败，请稍后重试"
        )
        return self._normalize_review(result)

    def generate_candidates(self, existing_titles: list[str], count: int = 3) -> list[dict]:
        """AI 自主生成面试知识条目候选（仍需经 review 审核后才会入库）。"""
        titles_text = "\n".join(f"- {t}" for t in existing_titles) or "（暂无）"
        system = GENERATE_SYSTEM_PROMPT.format(
            count=count, titles=titles_text, categories="、".join(KNOWLEDGE_CATEGORIES)
        )
        user = f"请生成 {count} 条新的面试知识条目。"
        result = parse_llm_json(
            self._invoke_llm(system, user), "AI 生成结果解析失败，请稍后重试"
        )
        entries = result.get("entries", [])
        if not isinstance(entries, list):
            raise ValueError("AI 生成结果格式异常")
        return [
            {
                "title": str(item.get("title", "")).strip()[:50],
                "category": str(item.get("category", "")).strip(),
                "content": str(item.get("content", "")).strip(),
            }
            for item in entries
            if isinstance(item, dict) and str(item.get("content", "")).strip()
        ]

    def _normalize_review(self, result: dict) -> dict:
        category = str(result.get("category", "")).strip()
        if category not in KNOWLEDGE_CATEGORIES:
            category = "general"
        return {
            "related": bool(result.get("related")),
            "duplicate": bool(result.get("duplicate")),
            "category": category,
            "title": str(result.get("title", "")).strip()[:50],
            "reason": str(result.get("reason", "")).strip(),
        }

    def _invoke_llm(self, system: str, user: str) -> str:
        if self._llm is None:
            self._llm = ChatTongyi(
                model=settings.llm_model,
                temperature=0.1,  # 审核判定要稳定，低温
                top_p=0.7,
            )
        messages = [
            SystemMessage(content=system),
            HumanMessage(content=user),
        ]
        response = self._llm.invoke(messages)
        return response.content or ""
