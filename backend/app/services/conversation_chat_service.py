from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

from langchain_community.chat_models import ChatTongyi
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from app.services.conversation_store_service import ConversationStoreService
from app.services.document_index_service import DocumentIndexService
from app.services.context_compression_service import ContextCompressionService
from app.services.context_verification_service import ContextVerificationService
from app.core.config import settings
from app.tools import MultiplyTool, WebSearchTool, WordDocumentTool, JdParserTool, ResumeScoreTool, ProjectRewriteTool, MockInterviewTool
from app.utils import ResourceUtils


@dataclass
class ConversationChatService:
    conversation_store_service: ConversationStoreService
    document_index_service: DocumentIndexService
    context_compression_service: ContextCompressionService = field(default_factory=ContextCompressionService)
    context_verification_service: ContextVerificationService = field(default_factory=ContextVerificationService)

    _IMAGE_MARKDOWN_PATTERN = re.compile(r"!\[[^\]]*\]\((/conversation/image/[^)\s]+)\)")
    _MAX_HISTORY_MESSAGES = 20  # 历史消息压缩阈值

    def __post_init__(self):
        self.qa_llm = None
        self.agent_llm = None
        self.vl_llm = None
        self.tool_dic = None
        self.qa_prompt = None
        self.agent_prompt = None
        self.multimodal_system_prompt = None

    def chat(self, conversation_id: str, query: str, mode: str = "agent", image_urls: list[str] | None = None) -> dict:
        normalized_mode = mode if mode in {"agent", "rag", "memory"} else "agent"
        effective_query = (query or "").strip() or "请描述图片内容。"
        normalized_image_urls = [item for item in (image_urls or []) if item]
        self.conversation_store_service.ensure_conversation_exists(conversation_id)
        self._ensure_initialized()

        # 1. 获取对话历史并进行上下文压缩
        history = self.conversation_store_service.get_conversation_messages(conversation_id)
        compressed_history = self.context_compression_service.compress_history(
            history, max_messages=self._MAX_HISTORY_MESSAGES
        )

        # 2. 检索上下文（含混合检索，返回详细来源）
        if normalized_mode == "memory":
            context = ""
            source_docs = []
        else:
            context_result = self.document_index_service.get_context_with_details(
                conversation_id, effective_query, limit=4
            )
            context = context_result["context"]
            source_docs = context_result["documents"]

        user_content = self._compose_user_content(effective_query, normalized_image_urls)

        # 3. 生成回答
        if normalized_image_urls:
            answer = self._invoke_multimodal(compressed_history, context, effective_query, normalized_image_urls)
        else:
            prompt = self.agent_prompt if normalized_mode == "agent" else self.qa_prompt
            prompt_messages = prompt.invoke({
                "query": effective_query,
                "history": self._build_history(compressed_history),
                "context": context,
            }).to_messages()

            if normalized_mode == "agent":
                answer = self._invoke_agent(prompt_messages)
            else:
                answer = self.qa_llm.invoke(prompt_messages).content

        answer = answer or "抱歉，我暂时无法生成回答。"

        # 4. 上下文验证 + 引用添加（仅在有检索上下文时执行）
        verification_result = None
        cited_answer = answer
        if context and normalized_mode != "memory":
            try:
                verify_report = self.context_verification_service.generate_verification_report(
                    answer, context, source_docs
                )
                verification_result = verify_report["verification"]
                cited_answer = verify_report["cited_answer"]
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning("上下文验证异常，跳过: %s", exc)

        # 5. 存储历史（存原始回答，不含引用标记）
        self.conversation_store_service.append_message_pair(conversation_id, user_content, answer, normalized_mode)
        detail = self.conversation_store_service.get_conversation_detail(conversation_id)
        return {
            "answer": cited_answer,  # 返回带引用的回答
            "original_answer": answer,
            "conversation": detail,
            "verification": verification_result,
            "sources": source_docs,
        }

    def _ensure_initialized(self):
        if self.qa_llm is not None:
            return

        self.qa_llm = ChatTongyi(
            streaming=True,
            model="qwen-plus",
            temperature=0.8,
            top_p=0.7,
        )
        self.vl_llm = ChatTongyi(
            model=settings.multimodal_model,
            temperature=0.7,
        )
        tools = [MultiplyTool(), WebSearchTool(), WordDocumentTool(), JdParserTool(), ResumeScoreTool(), ProjectRewriteTool(), MockInterviewTool()]
        self.tool_dic = {tool.name: tool for tool in tools}
        self.agent_llm = self.qa_llm.bind_tools(tools)

        self.qa_prompt = ChatPromptTemplate.from_messages([
            ("system", "你是一个专业的问答助手。你将收到聊天历史、会话文档上下文和用户问题。请优先基于当前会话文档回答；如果上下文为空或不足，可以明确说明并结合通用知识补充。"),
            MessagesPlaceholder("history"),
            ("human", "相关文档内容：{context}\n\n用户问题：{query}"),
        ])

        self.agent_prompt = ChatPromptTemplate.from_messages([
            ("system", "你是一个专业的求职助手机器人。你将获得当前会话的相关文档、聊天历史和用户问题。\n请优先利用当前会话文档回答。\n可用工具：\n- web_search_tool：搜索最新网页信息\n- word_document_tool：生成 Word 文档\n- jd_parser_tool：分析职位描述（JD），提取关键词、要求、加分项等\n- resume_score_tool：根据 JD 对简历进行结构化评分\n- project_rewrite_tool：优化简历中的项目经历描述\n- mock_interview_tool：根据 JD 和简历生成面试题\n用户如果需要分析 JD、评分简历、优化项目或模拟面试，请主动调用对应工具。"),
            MessagesPlaceholder("history"),
            ("human", "相关的文档内容：{context}\n\n用户的问题：{query}"),
        ])
        self.multimodal_system_prompt = (
            "你是一个专业的多模态问答助手。你将获得当前会话的聊天历史、相关文档上下文、"
            "以及用户上传的图片和问题。请优先结合图片与当前会话文档回答；如果文档上下文不足，"
            "请明确说明后再给出基于图片本身的判断。"
        )

    def _invoke_agent(self, messages: list) -> str:
        chain_output = ""
        for _ in range(3):
            response = self.agent_llm.invoke(messages)
            messages.append(response)
            tool_calls = response.tool_calls or []
            if not tool_calls:
                chain_output = response.content
                break

            for tool_call in tool_calls:
                tool = self.tool_dic.get(tool_call.get("name"))
                if tool is None:
                    content = f"工具不存在：{tool_call.get('name')}"
                else:
                    content = tool.invoke(tool_call.get("args"))
                messages.append(ToolMessage(tool_call_id=tool_call.get("id"), content=content))
        return chain_output

    def _invoke_multimodal(self, history: list[dict], context: str, query: str, image_urls: list[str]) -> str:
        content_parts = []
        for image_url in image_urls:
            content_parts.append({
                "image": self._resolve_image_path(image_url),
            })

        content_parts.append({
            "text": f"相关文档内容：{context}\n\n用户问题：{query}",
        })

        messages = [SystemMessage(content=[{"text": self.multimodal_system_prompt}])]
        messages.extend(self._build_multimodal_history(history))
        messages.append(HumanMessage(content=content_parts))
        response = self.vl_llm.invoke(messages)
        return self._extract_multimodal_text(response.content)

    def _resolve_image_path(self, image_url: str) -> str:
        conversation_id, filename = self._parse_image_url(image_url)
        image_dir = ResourceUtils.get_resource_path(os.path.join("uploads", "images", conversation_id))
        image_path = os.path.join(image_dir, filename)
        if not os.path.isfile(image_path):
            raise ValueError(f"图片文件不存在: {image_url}")
        return image_path

    @classmethod
    def _parse_image_url(cls, image_url: str) -> tuple[str, str]:
        parts = image_url.strip("/").split("/")
        if len(parts) != 4 or parts[0] != "conversation" or parts[1] != "image":
            raise ValueError("非法图片地址")
        conversation_id, filename = parts[2], parts[3]
        if re.fullmatch(r"[A-Za-z0-9_.-]+", conversation_id) is None:
            raise ValueError("非法图片地址")
        if re.fullmatch(r"[A-Za-z0-9_.-]+", filename) is None:
            raise ValueError("非法图片地址")
        return conversation_id, filename

    @classmethod
    def _compose_user_content(cls, query: str, image_urls: list[str]) -> str:
        lines = [f"![image]({image_url})" for image_url in image_urls]
        if query:
            lines.append(query)
        return "\n".join(lines)

    @classmethod
    def _extract_image_urls(cls, content: str) -> list[str]:
        return cls._IMAGE_MARKDOWN_PATTERN.findall(content or "")

    @classmethod
    def _strip_image_markdown(cls, content: str) -> str:
        return cls._IMAGE_MARKDOWN_PATTERN.sub("", content or "").strip()

    @classmethod
    def _extract_multimodal_text(cls, content: Any) -> str:
        """从多模态响应的 content 中提取纯文本。

        DashScope MultiModalConversation 返回的 assistant content 格式为：
            [{"text": "回答内容"}]
        需要把这种列表展开成纯字符串。
        """
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

    def _build_multimodal_history(self, history: list[dict]) -> list:
        messages = []
        for item in history:
            role = item.get("role")
            content = item.get("content", "")
            if role == "user":
                image_urls = self._extract_image_urls(content)
                clean_text = self._strip_image_markdown(content)
                if image_urls:
                    parts = []
                    for image_url in image_urls:
                        try:
                            parts.append({
                                "image": self._resolve_image_path(image_url),
                            })
                        except Exception:
                            continue
                    if clean_text:
                        parts.append({"type": "text", "text": clean_text})
                    if parts:
                        messages.append(HumanMessage(content=parts))
                        continue
                    content = clean_text

            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                messages.append(AIMessage(content=content))
        return messages

    @staticmethod
    def _build_history(history: list[dict]) -> list:
        messages = []
        for item in history:
            role = item.get("role")
            content = ConversationChatService._strip_image_markdown(item.get("content", ""))
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                messages.append(AIMessage(content=content))
        return messages