from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


def build_agent_system_prompt() -> str:
    """构建 Agent 系统提示（含动态技能清单）。

    注意：返回值会作为 ChatPromptTemplate 的模板文本，动态内容里的
    花括号必须转义成 {{...}}，否则会被当成模板变量导致所有请求报错。
    """
    return (
        "你是一个专业的求职助手机器人。你将获得当前会话的相关文档、聊天历史和用户问题。\n"
        "请优先利用当前会话文档回答。\n"
        "可用工具：\n"
        "- web_search_tool：搜索最新网页信息\n"
        "- word_document_tool：生成 Word 文档\n"
        "- html_document_tool：生成并保存 HTML 文件，返回下载链接；内容较长时用 start/append/finish 分块写入\n"
        "- jd_parser_tool：分析职位描述（JD），提取关键词、要求、加分项等\n"
        "- resume_score_tool：根据 JD 对简历进行结构化评分\n"
        "- project_rewrite_tool：优化简历中的项目经历描述\n"
        "- mock_interview_tool：根据 JD 和简历生成面试题\n"
        "- load_skill：加载内置求职技能的完整操作指南\n"
        "用户如果需要分析 JD、评分简历、优化项目或模拟面试，请主动调用对应工具。\n\n"
        "【文件交付硬性规则】只要用户要求得到一份文件（简历、报告、页面等），"
        "必须先调用 word_document_tool 或 html_document_tool 落盘，再把工具返回的下载链接写进回答。"
        "严禁把整份 HTML 或长文档源码直接贴到对话里——前端会剥离标签，用户只能看到乱码或空白。"
        "回答里除了链接，只写简短的交付说明。\n\n"
        "【make-resume 专属规则】调用 load_skill 加载 make-resume 后，"
        "必须按指南先把模板骨架写全（分块调用 html_document_tool），最后 finish 落盘，"
        "再给出下载链接与简短说明。不要因为内容长就只输出部分 HTML。\n\n"
        "内置求职技能（ASu-skills）：当用户任务匹配以下技能场景时，"
        "必须先调用 load_skill 加载该技能的完整指南，再严格按指南执行：\n"
        + skill_loader.catalog_prompt().replace("{", "{{").replace("}", "}}")
    )

from langchain_community.chat_models import ChatTongyi
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from app.services.conversation_store_service import ConversationStoreService
from app.services.document_index_service import DocumentIndexService
from app.services.context_compression_service import ContextCompressionService
from app.services.context_verification_service import ContextVerificationService
from app.core.config import settings
from app.tools import MultiplyTool, WebSearchTool, WordDocumentTool, HtmlDocumentTool, JdParserTool, ResumeScoreTool, ProjectRewriteTool, MockInterviewTool, SkillTool
from app.utils import ResourceUtils
from app.utils import skill_loader


@dataclass
class ConversationChatService:
    conversation_store_service: ConversationStoreService
    document_index_service: DocumentIndexService
    context_compression_service: ContextCompressionService = field(default_factory=ContextCompressionService)
    context_verification_service: ContextVerificationService = field(default_factory=ContextVerificationService)

    _IMAGE_MARKDOWN_PATTERN = re.compile(r"!\[[^\]]*\]\((/conversation/image/[^)\s]+)\)")
    _MAX_HISTORY_MESSAGES = 20  # 历史消息压缩阈值
    # 工具轮上限：make-resume 需要 load_skill 取指南 -> 多块 html_document_tool 写入 -> 收尾，
    # 3 轮不够用，模型会在没落盘的情况下被强制收尾（表现就是"生成不出 HTML 文件"）。
    _MAX_TOOL_ROUNDS = 6

    def __post_init__(self):
        self.qa_llm = None
        self.agent_llm = None
        self.vl_llm = None
        self.tool_dic = None
        self.qa_prompt = None
        self.agent_prompt = None
        self.multimodal_system_prompt = None

    def _prepare_chat(self, conversation_id, query, mode, image_urls):
        """公共前置流程：模式归一、历史压缩、上下文检索、用户消息组装。"""
        normalized_mode = mode if mode in {"agent", "rag", "memory"} else "agent"
        effective_query = (query or "").strip() or "请描述图片内容。"
        normalized_image_urls = [item for item in (image_urls or []) if item]
        self.conversation_store_service.ensure_conversation_exists(conversation_id)
        self._ensure_initialized()

        history = self.conversation_store_service.get_conversation_messages(conversation_id)
        compressed_history = self.context_compression_service.compress_history(
            history, max_messages=self._MAX_HISTORY_MESSAGES
        )

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
        return (
            normalized_mode,
            effective_query,
            normalized_image_urls,
            compressed_history,
            context,
            source_docs,
            user_content,
        )

    def chat(self, conversation_id: str, query: str, mode: str = "agent", image_urls: list[str] | None = None) -> dict:
        (
            normalized_mode,
            effective_query,
            normalized_image_urls,
            compressed_history,
            context,
            source_docs,
            user_content,
        ) = self._prepare_chat(conversation_id, query, mode, image_urls)

        # 3. 生成回答
        files: list[dict] = []
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
                answer, files = self._invoke_agent(prompt_messages)
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
            "files": files,
        }

    def chat_stream(self, conversation_id: str, query: str, mode: str = "agent", image_urls: list[str] | None = None):
        """流式问答生成器，产出 SSE 事件：meta -> delta* -> sources -> verification -> done。

        流式策略：
        - 首轮流式（bind_tools 状态下模型请求工具时通常无文本输出，delta 为空不影响前端）；
        - 首轮即给出答案（绝大多数请求）：token 级流式；
        - 命中工具：工具轮与最终回答走非流式，最终回答整段下发一次；
        - 多模态：整段下发；
        - 校验与引用标注依赖完整回答，在 done 前一次性下发。
        """
        (
            normalized_mode,
            effective_query,
            normalized_image_urls,
            compressed_history,
            context,
            source_docs,
            user_content,
        ) = self._prepare_chat(conversation_id, query, mode, image_urls)

        yield {"event": "meta", "data": {"conversation_id": conversation_id, "mode": normalized_mode}}

        files: list[dict] = []

        if normalized_image_urls:
            answer = self._invoke_multimodal(compressed_history, context, effective_query, normalized_image_urls)
            if answer:
                yield {"event": "delta", "data": {"text": answer}}
        else:
            prompt = self.agent_prompt if normalized_mode == "agent" else self.qa_prompt
            prompt_messages = prompt.invoke({
                "query": effective_query,
                "history": self._build_history(compressed_history),
                "context": context,
            }).to_messages()

            if normalized_mode == "agent":
                streamed_text = ""
                merged = None
                try:
                    for chunk in self.agent_llm.stream(prompt_messages):
                        merged = chunk if merged is None else merged + chunk
                        if chunk.content:
                            streamed_text += chunk.content
                            yield {"event": "delta", "data": {"text": chunk.content}}
                except Exception as exc:
                    logger.warning("流式生成异常: %s", exc)

                if merged is not None and merged.tool_calls:
                    messages = list(prompt_messages)
                    messages.append(AIMessage(content=merged.content or "", tool_calls=list(merged.tool_calls)))
                    final_answer, files = self._run_tool_rounds(messages)
                    answer = final_answer or streamed_text
                    if final_answer:
                        yield {"event": "delta", "data": {"text": final_answer}}
                else:
                    answer = streamed_text
            else:
                answer = self.qa_llm.invoke(prompt_messages).content
                if answer:
                    yield {"event": "delta", "data": {"text": answer}}

        answer = answer or "抱歉，我暂时无法生成回答。"

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
                logger.warning("上下文验证异常，跳过: %s", exc)

        # 与非流式一致：持久化原始回答（不含引用标记）
        self.conversation_store_service.append_message_pair(conversation_id, user_content, answer, normalized_mode)
        detail = self.conversation_store_service.get_conversation_detail(conversation_id)

        yield {"event": "sources", "data": {"sources": source_docs}}
        yield {"event": "verification", "data": {"verification": verification_result}}
        if files:
            yield {"event": "files", "data": {"files": files}}
        yield {
            "event": "done",
            "data": {
                "answer": cited_answer,
                "original_answer": answer,
                "conversation": detail,
                "files": files,
            },
        }

    def _run_tool_rounds(self, messages: list) -> tuple[str, list[dict]]:
        """首轮流式命中工具调用后，继续执行工具轮并返回（最终回答，本轮落盘的文件产物）。"""
        files: list[dict] = []
        last_text = ""
        for _ in range(self._MAX_TOOL_ROUNDS):
            response = self.agent_llm.invoke(messages)
            messages.append(response)
            tool_calls = response.tool_calls or []
            if not tool_calls:
                return response.content or last_text, files

            if response.content:
                last_text = response.content

            for tool_call in tool_calls:
                tool = self.tool_dic.get(tool_call.get("name"))
                if tool is None:
                    content = f"工具不存在：{tool_call.get('name')}"
                else:
                    try:
                        content = tool.invoke(tool_call.get("args"))
                    except Exception as exc:
                        logger.warning("工具 %s 执行异常: %s", tool_call.get("name"), exc)
                        content = f"工具执行失败：{exc}"
                artifact = self._collect_artifact(content)
                if artifact:
                    files.append(artifact)
                messages.append(ToolMessage(tool_call_id=tool_call.get("id"), content=content))
        # 轮次用尽：不要返回空串让用户看到"AI 什么都没说"，给出已收集到的最后一段文本
        return last_text, files

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
        tools = [MultiplyTool(), WebSearchTool(), WordDocumentTool(), HtmlDocumentTool(), JdParserTool(), ResumeScoreTool(), ProjectRewriteTool(), MockInterviewTool(), SkillTool()]
        self.tool_dic = {tool.name: tool for tool in tools}
        self.agent_llm = self.qa_llm.bind_tools(tools)

        self.qa_prompt = ChatPromptTemplate.from_messages([
            ("system", "你是一个专业的问答助手。你将收到聊天历史、会话文档上下文和用户问题。请优先基于当前会话文档回答；如果上下文为空或不足，可以明确说明并结合通用知识补充。"),
            MessagesPlaceholder("history"),
            ("human", "相关文档内容：{context}\n\n用户问题：{query}"),
        ])

        self.agent_prompt = ChatPromptTemplate.from_messages([
            ("system", build_agent_system_prompt()),
            MessagesPlaceholder("history"),
            ("human", "相关的文档内容：{context}\n\n用户的问题：{query}"),
        ])
        self.multimodal_system_prompt = (
            "你是一个专业的多模态问答助手。你将获得当前会话的聊天历史、相关文档上下文、"
            "以及用户上传的图片和问题。请优先结合图片与当前会话文档回答；如果文档上下文不足，"
            "请明确说明后再给出基于图片本身的判断。"
        )

    @classmethod
    def _collect_artifact(cls, content: Any) -> dict | None:
        """从工具结果里抽取 html_document_tool 返回的 artifact（非 JSON 或没有 artifact 则忽略）。"""
        if not isinstance(content, str) or "artifact" not in content:
            return None
        try:
            payload = json.loads(content)
        except (ValueError, TypeError):
            return None
        artifact = payload.get("artifact") if isinstance(payload, dict) else None
        if not isinstance(artifact, dict):
            return None
        return {
            "name": artifact.get("name", ""),
            "url": artifact.get("url", ""),
            "size": artifact.get("size", 0),
            "format": artifact.get("format", "html"),
        }

    def _invoke_agent(self, messages: list) -> tuple[str, list[dict]]:
        """执行工具轮直到拿到最终回答，同时回收本轮落盘的文件产物。"""
        chain_output = ""
        files: list[dict] = []
        for _ in range(self._MAX_TOOL_ROUNDS):
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
                    try:
                        content = tool.invoke(tool_call.get("args"))
                    except Exception as exc:
                        # 工具错误作为字符串回传给模型：保证消息序列完整，让模型自行决定下一步
                        logger.warning("工具 %s 执行异常: %s", tool_call.get("name"), exc)
                        content = f"工具执行失败：{exc}"
                artifact = self._collect_artifact(content)
                if artifact:
                    files.append(artifact)
                messages.append(ToolMessage(tool_call_id=tool_call.get("id"), content=content))
        return chain_output, files

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