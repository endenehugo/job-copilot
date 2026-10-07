from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ContextCompressionService:
    """上下文压缩与关键信息提取服务。

    职责：
    1. 将长对话历史压缩为简洁摘要，减少 Token 占用
    2. 从对话中提取关键实体、事实和需求
    3. 为多轮对话提供结构化的上下文摘要
    """

    _compressor_llm = None

    # ---- 公开方法 ----

    def compress_history(self, history: list[dict], max_messages: int = 20) -> list[dict]:
        """压缩过长的对话历史。

        如果消息数超过 max_messages，将最早的消息压缩为摘要，
        保留最近的消息保持完整。

        Args:
            history: 对话历史 [(role, content), ...]
            max_messages: 保留的最大消息数

        Returns:
            压缩后的历史：如果未超限则原样返回；
            如果超限则返回 [summary_msg, ...recent_msgs]
        """
        if len(history) <= max_messages:
            return history

        # 需要压缩
        compress_count = len(history) - max_messages
        compress_messages = history[:compress_count]
        recent_messages = history[compress_count:]

        summary = self._summarize_messages(compress_messages)
        summary_msg = {
            "role": "system",
            "content": f"[上下文摘要] 以下是较早对话的摘要：\n{summary}",
        }
        logger.info("对话历史压缩完成：%d 条 → 摘要 + %d 条", compress_count, len(recent_messages))
        return [summary_msg] + recent_messages

    def extract_key_information(self, history: list[dict]) -> str:
        """从对话历史中提取关键信息（实体、事实、需求）。

        Args:
            history: 完整的对话历史

        Returns:
            结构化的关键信息文本
        """
        if not history:
            return ""

        conversation_text = self._format_history(history)
        llm = self._get_llm()

        prompt = (
            "你是一个信息提取专家。请从以下对话中提取关键信息。\n\n"
            "对话记录：\n"
            f"{conversation_text}\n\n"
            "请按以下格式输出关键信息（如果没有某类信息则写'无'）：\n"
            "【涉及文档】\n- ...\n\n"
            "【关键事实】\n- ...\n\n"
            "【用户需求/目标】\n- ...\n\n"
            "【已确认的信息】\n- ...\n\n"
            "【待确认/待补充的信息】\n- ...\n"
        )

        try:
            result = llm.invoke(prompt).content.strip()
            return result
        except Exception as exc:
            logger.warning("提取关键信息失败: %s", exc)
            return ""

    def build_compressed_context(
        self,
        history: list[dict],
        compressed_key_info: str | None = None,
    ) -> str:
        """构建用于 Prompt 的压缩上下文。

        将历史中的压缩摘要和关键信息整合为一段上下文文本。

        Args:
            history: 对话历史（可能已包含压缩摘要）
            compressed_key_info: 之前提取的关键信息（可选）

        Returns:
            压缩后的上下文字符串
        """
        parts = []

        # 提取压缩摘要
        summary_text = self._extract_summary_from_history(history)
        if summary_text:
            parts.append(f"[对话历史摘要]\n{summary_text}")

        # 提取关键信息
        if compressed_key_info:
            parts.append(f"[关键信息]\n{compressed_key_info}")
        elif len(history) > 6:
            # 历史较长时自动提取关键信息
            key_info = self.extract_key_information(history)
            if key_info:
                parts.append(f"[关键信息]\n{key_info}")

        return "\n\n".join(parts)

    # ---- 内部方法 ----

    def _summarize_messages(self, messages: list[dict]) -> str:
        """将一批消息压缩为摘要。"""
        if not messages:
            return ""

        text = self._format_history(messages)
        llm = self._get_llm()

        prompt = (
            "请将以下对话内容压缩为简洁的摘要（中文，200 字以内），"
            "保留关键事实、用户需求和已得出的结论：\n\n"
            f"{text}\n\n摘要："
        )

        try:
            result = llm.invoke(prompt).content.strip()
            return result[:500]
        except Exception as exc:
            logger.warning("消息摘要生成失败: %s", exc)
            return text[:300] + "……"

    def _extract_summary_from_history(self, history: list[dict]) -> str:
        """从历史中提取已有的压缩摘要。"""
        for msg in history:
            if msg.get("role") == "system" and "[上下文摘要]" in (msg.get("content") or ""):
                content = msg.get("content", "")
                # 提取摘要正文
                if "以下是较早对话的摘要：" in content:
                    return content.split("以下是较早对话的摘要：", 1)[-1].strip()
        return ""

    def _format_history(self, messages: list[dict]) -> str:
        """将消息列表格式化为可读文本。"""
        lines = []
        role_map = {"user": "用户", "assistant": "助手"}
        for msg in messages:
            role = role_map.get(msg.get("role", ""), msg.get("role", ""))
            content = (msg.get("content") or "").strip()
            if content:
                lines.append(f"{role}: {content}")
        return "\n".join(lines)

    def _get_llm(self):
        if self._compressor_llm is None:
            from langchain_community.chat_models import ChatTongyi
            self._compressor_llm = ChatTongyi(
                model="qwen-plus",
                temperature=0.3,
                top_p=0.5,
            )
        return self._compressor_llm
