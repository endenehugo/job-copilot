from __future__ import annotations

from app.core.config import settings

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

    def compress_history(
        self,
        history: list[dict],
        old_summary: str = "",
        max_messages: int = 20,
    ) -> tuple[list[dict], str, int]:
        """增量压缩对话历史。

        Args:
            history: 水位线之后的新消息（每条需含 id 字段）
            old_summary: 上次持久化的摘要
            max_messages: 保留的最大消息数

        Returns:
            (压缩后消息列表, 新摘要, 新水位线 id)
            - 压缩后消息列表：[summary_msg?] + recent_msgs
            - 新摘要：合并后的摘要文本（未变化则返回 old_summary）
            - 新水位线：最后一条被压缩消息的 id（未变化则返回 0）
        """
        if len(history) <= max_messages:
            result = []
            if old_summary:
                result.append(self._make_summary_msg(old_summary))
            return (result + history, old_summary, 0)

        compress_count = len(history) - max_messages
        compress_messages = history[:compress_count]
        recent_messages = history[compress_count:]
        new_watermark = compress_messages[-1].get("id", 0)

        new_summary = self._merge_summary(old_summary, compress_messages)
        summary_msg = self._make_summary_msg(new_summary)

        logger.info(
            "增量摘要完成：旧摘要 %d 字 + %d 条新消息 → 新摘要 %d 字，水位线 → %d",
            len(old_summary), compress_count, len(new_summary), new_watermark,
        )
        return ([summary_msg] + recent_messages, new_summary, new_watermark)

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

    @staticmethod
    def _make_summary_msg(summary: str) -> dict:
        return {
            "role": "system",
            "content": f"[上下文摘要] 以下是较早对话的摘要：\n{summary}",
        }

    def _merge_summary(self, old_summary: str, new_messages: list[dict]) -> str:
        """将旧摘要与新溢出消息合并为更新后的摘要。"""
        new_text = self._format_history(new_messages)
        if not new_text:
            return old_summary

        llm = self._get_llm()

        if old_summary:
            prompt = (
                "你已有一份对话摘要，现在需要将新的对话内容合并进去，生成更新后的摘要。\n"
                "要求：中文，300 字以内，保留关键事实、用户需求、已得出的结论，去掉过时信息。\n\n"
                f"【已有摘要】\n{old_summary}\n\n"
                f"【新增对话】\n{new_text}\n\n"
                "更新后的摘要："
            )
        else:
            prompt = (
                "请将以下对话内容压缩为简洁的摘要（中文，300 字以内），"
                "保留关键事实、用户需求和已得出的结论：\n\n"
                f"{new_text}\n\n摘要："
            )

        try:
            result = llm.invoke(prompt).content.strip()
            return result[:800]
        except Exception as exc:
            logger.warning("增量摘要生成失败，保留旧摘要: %s", exc)
            return old_summary

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
            from app.core.llm_factory import build_chat_llm
            self._compressor_llm = build_chat_llm(
                model=settings.llm_small_model,
                temperature=0.3,
                top_p=0.5,
            )
        return self._compressor_llm
