from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ContextVerificationService:
    """上下文验证与引用展示服务。

    职责：
    1. 验证 LLM 回答中的断言是否有所提供上下文的支撑
    2. 为回答添加引用标记，标明信息来源
    3. 生成结构化验证报告
    """

    _verifier_llm = None

    # ---- 公开方法 ----

    def verify_answer(self, answer: str, context: str, source_docs: list[dict] | None = None) -> dict:
        """验证回答是否基于给定上下文。

        Args:
            answer: LLM 生成的回答文本
            context: 检索到的上下文字符串
            source_docs: 来源文档详情列表，每项含 content、source_name

        Returns:
            {
                "is_supported": bool,       # 整体是否基于上下文
                "unsupported_claims": [],   # 无支撑的断言列表
                "supported_claims": [],     # 有支撑的断言列表
                "hallucination_risk": str,  # "low" | "medium" | "high"
                "verification_detail": str,  # 验证说明文本
            }
        """
        if not answer or not context:
            return {
                "is_supported": True,
                "unsupported_claims": [],
                "supported_claims": [],
                "hallucination_risk": "low",
                "verification_detail": "无回答或无上下文，跳过验证。",
            }

        result = self._llm_verify(answer, context)

        # 计算幻觉风险等级
        unsupported = result.get("unsupported_claims", [])
        total_claims = len(unsupported) + len(result.get("supported_claims", []))
        if total_claims == 0:
            risk = "low"
        else:
            ratio = len(unsupported) / total_claims
            if ratio >= 0.5:
                risk = "high"
            elif ratio >= 0.25:
                risk = "medium"
            else:
                risk = "low"

        result["hallucination_risk"] = risk
        return result

    def add_citations(self, answer: str, source_docs: list[dict]) -> str:
        """为回答添加引用标记。

        解析回答中的关键断言，匹配到对应的来源文档，
        在文本中插入引用角标 [1]、[2] 等。

        Args:
            answer: 原始回答文本
            source_docs: 来源文档列表 [{"content": ..., "source_name": ...}, ...]

        Returns:
            带引用标记的回答文本
        """
        if not source_docs:
            return answer

        try:
            citation_map = self._build_citation_map(answer, source_docs)
            if not citation_map:
                return answer

            # 在回答中插入引用标记（按位置逆序插入以避免偏移问题）
            tagged_answer = answer
            # 对每个来源，找到匹配的断言位置并插入引用标签
            insertions = []
            for source_idx, (source_name, matched_claims) in enumerate(citation_map.items()):
                for claim_text in matched_claims:
                    # 在断言文本后插入引用标记
                    citation_tag = f"[{source_idx + 1}]"
                    if claim_text in tagged_answer:
                        pos = tagged_answer.find(claim_text) + len(claim_text)
                        insertions.append((pos, citation_tag))

            # 按位置逆序插入
            insertions.sort(key=lambda x: x[0], reverse=True)
            for pos, tag in insertions:
                # 检查该位置是否已存在标签
                if pos < len(tagged_answer) and tagged_answer[pos:pos + len(tag)] != tag:
                    tagged_answer = tagged_answer[:pos] + tag + tagged_answer[pos:]

            # 添加引用来源说明
            if citation_map:
                tagged_answer += "\n\n---\n**📖 参考来源**\n"
                for idx, (source_name, _) in enumerate(citation_map.items()):
                    display_name = source_name if source_name != "未知文档" else f"来源 {idx + 1}"
                    tagged_answer += f"\n[{idx + 1}] {display_name}"

            return tagged_answer
        except Exception as exc:
            logger.warning("添加引用标记失败: %s", exc)
            return answer

    def generate_verification_report(self, answer: str, context: str, source_docs: list[dict]) -> dict:
        """生成完整的验证报告。

        包含验证结果和带引用的回答。
        """
        verify_result = self.verify_answer(answer, context, source_docs)
        cited_answer = self.add_citations(answer, source_docs)

        return {
            "verification": verify_result,
            "cited_answer": cited_answer,
            "original_answer": answer,
        }

    # ---- 内部方法 ----

    def _llm_verify(self, answer: str, context: str) -> dict:
        """使用 LLM 验证回答与上下文的一致性。"""
        llm = self._get_llm()

        prompt = (
            "你是一个回答验证专家。请判断以下回答中的每个关键断言是否都有上下文支撑。\n\n"
            f"上下文：\n{context[:3000]}\n\n"
            f"回答：\n{answer}\n\n"
            "请按以下 JSON 格式输出（不要包含 markdown 代码块标记）：\n"
            "{\n"
            '  "supported_claims": ["有上下文支撑的断言列表"],\n'
            '  "unsupported_claims": ["没有上下文支撑的断言列表"],\n'
            '  "verification_detail": "整体验证说明"\n'
            "}"
        )

        try:
            result = llm.invoke(prompt).content.strip()
            return self._parse_verification_json(result)
        except Exception as exc:
            logger.warning("LLM 验证失败: %s", exc)
            return {
                "supported_claims": [],
                "unsupported_claims": [],
                "verification_detail": f"验证过程出现异常: {exc}",
            }

    def _build_citation_map(self, answer: str, source_docs: list[dict]) -> dict[str, list[str]]:
        """构建回答断言到来源文档的映射。

        Returns:
            {source_name: [matched_claims, ...], ...}
        """
        citation_map = {}
        for doc in source_docs:
            source_name = doc.get("source_name", "未知文档")
            content = doc.get("content", "")
            if not content:
                continue

            # 从上下文中提取关键短语（长度 >= 8 的句子片段）
            key_phrases = self._extract_key_phrases(content)
            matched_claims = []

            for phrase in key_phrases:
                if phrase in answer and phrase not in matched_claims:
                    matched_claims.append(phrase)

            if matched_claims:
                citation_map[source_name] = matched_claims

        return citation_map

    @staticmethod
    def _extract_key_phrases(text: str, min_len: int = 8) -> list[str]:
        """从文本中提取关键短语用于匹配。

        提取长度 >= min_len 的中文句子或关键片段。
        """
        # 按句号、问号、感叹号、换行切分
        sentences = re.split(r"[。！？\n]+", text)
        phrases = []
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) >= min_len:
                phrases.append(sentence)
            # 也提取逗号分隔的子句
            clauses = re.split(r"[，；、]", sentence)
            for clause in clauses:
                clause = clause.strip()
                if min_len <= len(clause) <= len(sentence) // 2 + 5:
                    phrases.append(clause)

        # 去重并按长度排序（优先匹配较长文本）
        phrases = list(set(phrases))
        phrases.sort(key=len, reverse=True)
        return phrases[:50]  # 最多取 50 个

    @staticmethod
    def _parse_verification_json(text: str) -> dict:
        """解析验证结果的 JSON。"""
        # 尝试直接从文本中提取 JSON
        json_match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if json_match:
            try:
                import json
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        # 尝试从 markdown 代码块提取
        code_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if code_match:
            try:
                import json
                return json.loads(code_match.group(1))
            except json.JSONDecodeError:
                pass

        # 降级：提取关键字段
        result = {"supported_claims": [], "unsupported_claims": [], "verification_detail": text[:500]}
        supported_match = re.search(r'"supported_claims"\s*:\s*\[(.*?)\]', text, re.DOTALL)
        if supported_match:
            items = re.findall(r'"([^"]*)"', supported_match.group(1))
            result["supported_claims"] = items

        unsupported_match = re.search(r'"unsupported_claims"\s*:\s*\[(.*?)\]', text, re.DOTALL)
        if unsupported_match:
            items = re.findall(r'"([^"]*)"', unsupported_match.group(1))
            result["unsupported_claims"] = items

        return result

    def _get_llm(self):
        if self._verifier_llm is None:
            from langchain_community.chat_models import ChatTongyi
            self._verifier_llm = ChatTongyi(
                model="qwen-plus",
                temperature=0.1,
                top_p=0.3,
            )
        return self._verifier_llm
