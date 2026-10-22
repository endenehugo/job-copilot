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
        "请优先利用当前会话文档回答。\n\n"
        "【事实边界·优先级最高】凡涉及用户的个人信息、教育背景、工作或实习经历、项目、"
        "数字指标（QPS/耗时/百分比等）、技术栈，只能使用两类来源："
        "① 用户在对话中明确说过的内容；② 用户上传的文档。\n"
        "上下文里标为「内置知识库参考」的内容是通用方法论，不是用户的经历——"
        "禁止把其中的项目名、公司、数字、技术栈写进简历、项目描述、JD 分析或任何交付物。\n"
        "信息不足时直接向用户索取，不要推测、不要用同类项目或行业常见做法填充、不要编造数字。"
        "宁可少写一条，也不要造一条。\n"
        "如果当前会话文档与问题无关，直接说明缺少哪些信息，禁止凭常识「构建」或「推测」用户的项目画像。\n\n"
        "可用工具：\n"
        "- web_search_tool：搜索最新网页信息\n"
        "- resume_generate_tool：按会话材料生成排版完整的 HTML 简历（结构化抽取 + 模板渲染，推荐）\n"
        "- word_document_tool：生成 Word 文档\n"
        "- html_document_tool：生成并保存 HTML 文件，返回下载链接；内容较长时用 start/append/finish 分块写入\n"
        "- jd_parser_tool：分析职位描述（JD），提取关键词、要求、加分项等\n"
        "- resume_score_tool：根据 JD 对简历进行结构化评分\n"
        "- project_rewrite_tool：优化简历中的项目经历描述\n"
        "- mock_interview_tool：根据 JD 和简历生成面试题\n"
        "- load_skill：加载内置求职技能的完整操作指南\n"
        "用户如果需要分析 JD、评分简历、优化项目或模拟面试，请主动调用对应工具。\n\n"
        "【简历生成规则·必须执行】用户要求生成或重写简历时：\n"
        "① 必须先调用 resume_generate_tool。它按固定模板渲染、一次调用完成，"
        "比手写 HTML 快一个数量级，素材自动取自会话（上传的简历或用户口述）。\n"
        "② 不要自己预判「信息够不够」——先调工具，工具会返回 missing 清单和 audit_warnings，"
        "再据此向用户追问。预判会导致用户拿不到任何文件。\n"
        "③ **只有工具真的返回了 html_url，才能说「已生成/已落盘」并把链接写出来。**"
        "没有工具返回就声称生成属于编造，用户点开链接是 404，比直接说失败更糟。"
        "工具报错时如实转述原因和缺少的材料。\n"
        "④ 不需要先 load_skill 加载 make-resume，那套规范已内置在工具里。\n"
        "⑤ 只有用户明确要求自定义版式、或要的根本不是简历，才用 html_document_tool。\n"
        "工具返回的 audit_warnings（数字/技术栈在原文里找不到出处）要如实转述，"
        "missing 非空时向用户追问，禁止自己补字段。\n\n"
        "【文件交付硬性规则】只要用户要求得到一份文件（简历、报告、页面等），"
        "必须先调用 resume_generate_tool / word_document_tool / html_document_tool 落盘，"
        "再把工具返回的下载链接写进回答。"
        "严禁把整份 HTML 或长文档源码直接贴到对话里——前端会剥离标签，用户只能看到乱码或空白。"
        "回答里除了链接，只写简短的交付说明。\n\n"
        "内置求职技能（ASu-skills）：当用户任务匹配以下技能场景时，"
        "必须先调用 load_skill 加载该技能的完整指南，再严格按指南执行：\n"
        + skill_loader.catalog_prompt().replace("{", "{{").replace("}", "}}")
    )

from app.core.llm_factory import build_chat_llm
from app.core.model_scope import current_chat_model
from app.core.ownership import current_owner_key
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from app.services.conversation_store_service import ConversationStoreService
from app.services.document_index_service import DocumentIndexService
from app.services.context_compression_service import ContextCompressionService
from app.services.context_verification_service import ContextVerificationService
from app.core.config import settings
from app.tools import MultiplyTool, WebSearchTool, WordDocumentTool, HtmlDocumentTool, JdParserTool, ResumeScoreTool, ProjectRewriteTool, MockInterviewTool, SkillTool, ResumeGenerateTool
from app.utils import ResourceUtils
from app.utils import skill_loader


@dataclass
class ConversationChatService:
    conversation_store_service: ConversationStoreService
    document_index_service: DocumentIndexService
    context_compression_service: ContextCompressionService = field(default_factory=ContextCompressionService)
    context_verification_service: ContextVerificationService = field(default_factory=ContextVerificationService)

    _IMAGE_MARKDOWN_PATTERN = re.compile(r"!\[[^\]]*\]\((/conversation/image/[^)\s]+)\)")
    # 回答里"声称已经产出文件"的几种说法。用于兜住模型不调工具却报告成功的幻觉
    # （2026-10-22 实测：模型直接回"简历已生成并落盘，下载链接见上方工具返回结果"，
    # 而那个"上方"什么都没有，用户点开必然 404）。
    _FILE_CLAIM_PATTERN = re.compile(
        r"(已|已经)(为你)?(生成|落盘|保存|输出).{0,10}(简历|文件|文档|HTML)"
        r"|(简历|文件|文档|HTML).{0,10}(已|已经).{0,6}(生成|落盘|保存|交付)"
        r"|下载链接|点击下载|文件链接"
    )
    _MAX_HISTORY_MESSAGES = 12  # 历史消息压缩阈值（保留最近 6 轮逐字，更早走增量摘要）
    # 工具轮上限：make-resume 需要 load_skill 取指南 -> 多块 html_document_tool 写入 -> 收尾，
    # 3 轮不够用，模型会在没落盘的情况下被强制收尾（表现就是"生成不出 HTML 文件"）。
    _MAX_TOOL_ROUNDS = 6
    # 收尾调用里每条工具结果最多带多少字符：html_document_tool 之类会返回整页产物，
    # 全量塞进去只会把收尾调用也拖慢，摘要用的前 1200 字足够
    _TOOL_RESULT_PREVIEW_CHARS = 1200

    def __post_init__(self):
        self.qa_llm = None
        self.agent_llm = None
        self.vl_llm = None
        self.tool_dic = None
        self.tools = None
        self.qa_prompt = None
        self.agent_prompt = None
        self.multimodal_system_prompt = None
        # 记录 qa/agent 客户端当前绑定的模型，请求换了模型才重建
        self._qa_llm_model = None
        self._agent_llm_model = None

    def _get_qa_llm(self):
        """按本次请求所选模型取问答客户端（build_chat_llm 有 lru_cache，按模型切换近乎零成本）。"""
        model = current_chat_model()
        if self.qa_llm is None or self._qa_llm_model != model:
            self.qa_llm = build_chat_llm(
                streaming=True,
                model=model,
                temperature=0.8,
                top_p=0.7,
            )
            self._qa_llm_model = model
        return self.qa_llm

    def _get_agent_llm(self):
        """按本次请求所选模型取绑定工具后的 Agent 客户端。"""
        model = current_chat_model()
        if self.agent_llm is None or self._agent_llm_model != model:
            self._ensure_initialized()
            self.agent_llm = self._get_qa_llm().bind_tools(self.tools)
            self._agent_llm_model = model
        return self.agent_llm

    def _prepare_chat(self, conversation_id, query, mode, image_urls):
        """公共前置流程：模式归一、历史压缩、上下文检索、用户消息组装。"""
        normalized_mode = mode if mode in {"agent", "rag", "memory"} else "agent"
        effective_query = (query or "").strip() or "请描述图片内容。"
        normalized_image_urls = [item for item in (image_urls or []) if item]
        # 归属从请求作用域取：这条调用链太深（chat_stream → 这里），逐层透传必漏。
        # 用 writable 而非 exists：演示样本对访客可见，但对话会落消息，
        # 不拦的话第一个访客就能把样本聊脏
        self.conversation_store_service.ensure_conversation_writable(
            conversation_id, owner_key=current_owner_key()
        )
        self._ensure_initialized()

        old_summary, summarized_until_id = self.conversation_store_service.get_conversation_summary(conversation_id)
        history = self.conversation_store_service.get_messages_after(conversation_id, summarized_until_id)

        compressed_history, new_summary, new_watermark = self.context_compression_service.compress_history(
            history, old_summary=old_summary, max_messages=self._MAX_HISTORY_MESSAGES
        )

        if new_summary != old_summary and new_watermark > 0:
            self.conversation_store_service.save_conversation_summary(
                conversation_id, new_summary, new_watermark
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
        tool_warnings: list[str] = []
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
                answer, files, tool_warnings = self._invoke_agent(prompt_messages, effective_query)
            else:
                answer = self._get_qa_llm().invoke(prompt_messages).content

        answer = answer or self._fallback_answer(tool_warnings)
        answer = self._guard_fake_artifact_claim(answer, files)

        # 4. 上下文验证 + 引用添加（仅在有检索上下文时执行）
        #    有文件产物时跳过，理由同 chat_stream：回答里没有可校验的事实断言
        verification_result = None
        cited_answer = answer
        if context and normalized_mode != "memory" and not files:
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
        detail = self.conversation_store_service.get_conversation_detail(
            conversation_id, owner_key=current_owner_key()
        )
        return {
            "answer": cited_answer,  # 返回带引用的回答
            "original_answer": answer,
            "conversation": detail,
            "verification": verification_result,
            "sources": source_docs,
            "files": files,
            # 工具链异常结束的告警；API 层弹出后转为信封 degraded/warnings
            "warnings": tool_warnings,
        }

    def chat_stream(self, conversation_id: str, query: str, mode: str = "agent", image_urls: list[str] | None = None):
        """流式问答生成器，产出 SSE 事件：meta -> delta* -> sources -> files -> done -> verification。

        注意 verification 排在 done 之后：上下文校验要再跑一次 LLM（实测 3~8 秒），
        放在 done 前会让用户盯着"字已打完但没结束"的界面干等。引用标注依赖
        完整回答，因此随 verification 事件补发 cited_answer，由前端覆盖已显示的内容。

        流式策略：
        - 首轮流式（bind_tools 状态下模型请求工具时通常无文本输出，delta 为空不影响前端）；
        - 首轮即给出答案（绝大多数请求）：token 级流式；
        - 命中工具：工具轮与最终回答走非流式，最终回答整段下发一次；
        - 多模态：整段下发；
        - 校验与引用标注依赖完整回答，在 done 之后补发（不阻塞用户看到回答）。
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
        tool_warnings: list[str] = []

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
                    for chunk in self._get_agent_llm().stream(prompt_messages):
                        merged = chunk if merged is None else merged + chunk
                        if chunk.content:
                            streamed_text += chunk.content
                            yield {"event": "delta", "data": {"text": chunk.content}}
                except Exception as exc:
                    logger.warning("流式生成异常: %s", exc)
                    # 旧代码只写日志，前端收不到 error 事件，界面会静默显示残缺回答
                    yield {"event": "error", "data": {"message": "生成过程中断，已保留已生成的部分内容"}}

                if merged is not None and merged.tool_calls:
                    messages = list(prompt_messages)
                    messages.append(AIMessage(content=merged.content or "", tool_calls=list(merged.tool_calls)))
                    tool_result: dict = {}
                    # 透传工具进度事件：agent 模式的长工具链对用户可见
                    for ev in self._run_tool_rounds(messages, tool_result, effective_query):
                        yield ev
                    final_answer = tool_result.get("answer", "")
                    files = tool_result.get("files", [])
                    tool_warnings = tool_result.get("warnings", [])
                    answer = final_answer or streamed_text
                    if final_answer:
                        yield {"event": "delta", "data": {"text": final_answer}}
                else:
                    answer = streamed_text
            else:
                answer = self._get_qa_llm().invoke(prompt_messages).content
                if answer:
                    yield {"event": "delta", "data": {"text": answer}}

        answer = answer or self._fallback_answer(tool_warnings)
        answer = self._guard_fake_artifact_claim(answer, files)

        # 与非流式一致：持久化原始回答（不含引用标记）
        self.conversation_store_service.append_message_pair(conversation_id, user_content, answer, normalized_mode)
        detail = self.conversation_store_service.get_conversation_detail(
            conversation_id, owner_key=current_owner_key()
        )

        # done 先发：验证还要跑一次 LLM（实测 3~8 秒），不能让它挡在回答前面
        yield {"event": "sources", "data": {"sources": source_docs}}
        if files:
            yield {"event": "files", "data": {"files": files}}
        done_data = {
            # 引用标注依赖完整回答，随 verification 事件补发
            "answer": answer,
            "original_answer": answer,
            "conversation": detail,
            "files": files,
        }
        if tool_warnings:
            # 工具链异常结束（轮次耗尽/工具失败）不是正常完成的回答，必须让前端知道
            done_data["degraded"] = True
            done_data["warnings"] = tool_warnings
        yield {"event": "done", "data": done_data}

        # 产出文件的那一轮不做上下文校验：回答只是"已生成 + 下载链接"，
        # 没有可被文档反驳的事实断言，引用标注也无从标起。实测这一跳要再跑
        # 一次 LLM（5~33 秒）——纯烧额度，还让 SSE 连接多挂几十秒。
        if context and normalized_mode != "memory" and not files:
            try:
                verify_report = self.context_verification_service.generate_verification_report(
                    answer, context, source_docs
                )
                yield {
                    "event": "verification",
                    "data": {
                        "verification": verify_report["verification"],
                        "cited_answer": verify_report["cited_answer"],
                    },
                }
            except Exception as exc:
                logger.warning("上下文验证异常，跳过: %s", exc)

    @classmethod
    def _guard_fake_artifact_claim(cls, answer: str, files: list[dict]) -> str:
        """模型声称产出了文件、但实际没有任何产物时，把真实情况补在末尾。

        不去"纠正模型"（不可靠），只保证用户不会被引导去点一个不存在的链接。
        2026-10-22 实测：模型跳过工具直接回「简历已生成并落盘」，
        追问后才承认没调工具，用户拿到的只有一段文字。
        """
        if files or not answer:
            return answer
        if not cls._FILE_CLAIM_PATTERN.search(answer):
            return answer
        return (
            answer
            + "\n\n---\n\n"
            "【注意】本轮实际上**没有生成任何可下载的文件**：上面提到的文件并没有落盘，"
            "链接点开会是 404。请重新发送一次；如果仍然不出文件，"
            "就把要写的内容（学校、项目、联系方式）一次性说明清楚，我再重新生成。"
        )

    @staticmethod
    def _fallback_answer(warnings: list[str]) -> str:
        """回答为空时的兜底文案：说清发生了什么 + 下一步做什么。

        原来无论哪种情况都返回「抱歉，我暂时无法生成回答。」，把
        「模型返回空」「工具轮次耗尽」「工具执行失败」折叠成同一句，
        用户既不知道原因，也不知道该重试、该拆小需求，还是该换模型。
        2026-10-22 线上就有 HR 卡在这里。
        """
        if warnings:
            return (
                "这次没能给出完整回答：工具链没跑完（轮次用尽或工具执行失败）。\n\n"
                "已经执行的操作没有丢失。建议把要求拆小一点再试——"
                "比如先只生成简历里的「项目经历」或「技能」一个板块，而不是整份简历；"
                "内容越大，模型一次生成不完的概率越高。"
            )
        return (
            "这次模型没有返回内容，通常是瞬时故障。\n\n"
            "直接重试一次即可。如果连续失败，可以换一个模型，"
            "或把要求写得更具体（岗位、年限、要突出的项目），"
            "需求越模糊，模型越容易在生成途中空转。"
        )

    def _finalize_without_tools(self, query: str, tool_outputs: list[tuple[str, str]]) -> str:
        """工具轮次用尽后的收尾：不再给工具，让模型基于已有工具结果直接作答。

        没有这一步时，轮次耗尽只能返回最后一段中间文本或空串（用户看到
        "抱歉，我暂时无法生成回答"），前面几次工具调用的成本全部作废。
        这里用不带工具的客户端，避免它继续调工具又把轮次走完一遍。
        调用失败返回空串，由调用方维持原有的降级行为。
        """
        parts = []
        for name, content in tool_outputs:
            text = (content or "").strip()
            if not text:
                continue
            if len(text) > self._TOOL_RESULT_PREVIEW_CHARS:
                text = text[: self._TOOL_RESULT_PREVIEW_CHARS] + "…（已截断）"
            parts.append(f"【{name}】\n{text}")
        if not parts:
            return ""
        prompt = (
            "你是求职助手。本轮的工具调用次数已经用完，现在不能再调用任何工具。\n"
            "请只依据下面已经拿到的工具结果直接回答用户的问题；"
            "如果这些结果不足以回答，就说清楚缺少什么信息，不要编造。\n\n"
            f"用户问题：{query}\n\n"
            "已拿到的工具结果：\n\n" + "\n\n".join(parts)
        )
        try:
            return (self._get_qa_llm().invoke(prompt).content or "").strip()
        except Exception as exc:
            logger.warning("工具轮次用尽后的收尾调用失败: %s", exc)
            return ""

    def _run_tool_rounds(self, messages: list, result: dict, query: str = ""):
        """首轮流式命中工具调用后，继续执行工具轮。

        改为生成器：每个工具执行前产出 tool_status 事件（SSE 透传给前端，
        让 agent 模式的长工具链可见，不再对着空气泡干等）；最终回答与
        文件产物写入 result 字典。

        result 键：answer / files / warnings / exhausted（轮次是否用尽）。
        """
        files: list[dict] = []
        warnings: list[str] = []
        tool_outputs: list[tuple[str, str]] = []
        last_text = ""
        step = 0

        for round_no in range(self._MAX_TOOL_ROUNDS):
            # 工具轮是非流式 invoke：模型要吐完整工具参数（简历 HTML 十几 KB）才返回，
            # 实测静默 60~100 秒，期间下游一条事件都发不出来，界面看起来就是卡死。
            # 先发一条 thinking 让前端至少能显示"第几轮、已等多久"。
            yield {
                "event": "thinking",
                "data": {"round": round_no + 1, "max_rounds": self._MAX_TOOL_ROUNDS},
            }
            response = self._get_agent_llm().invoke(messages)
            messages.append(response)
            tool_calls = response.tool_calls or []
            if not tool_calls:
                result.update(
                    answer=response.content or last_text,
                    files=files,
                    warnings=warnings,
                    exhausted=False,
                )
                return

            if response.content:
                last_text = response.content

            for tool_call in tool_calls:
                name = tool_call.get("name") or ""
                step += 1
                yield {
                    "event": "tool_status",
                    "data": {"name": name, "step": step, "round": round_no + 1, "max_rounds": self._MAX_TOOL_ROUNDS},
                }
                content, failed = self._execute_tool_call(tool_call)
                if failed:
                    warnings.append(f"工具 {name} 执行失败")
                tool_outputs.append((name, content))
                artifact = self._collect_artifact(content)
                if artifact:
                    files.append(artifact)
                messages.append(ToolMessage(tool_call_id=tool_call.get("id"), content=content))

        # 轮次用尽：先做一次不带工具的收尾调用，让模型基于已执行完的工具结果作答。
        # 没有这一步时用户只会看到"抱歉，我暂时无法生成回答"，前几轮工具的成本全部作废。
        finalized = self._finalize_without_tools(query, tool_outputs)
        if finalized:
            warnings.append("工具调用已达轮次上限，回答由已执行工具的结果汇总生成")
            result.update(answer=finalized, files=files, warnings=warnings, exhausted=True)
            return

        # 收尾调用也失败：不要返回空串让用户看到"AI 什么都没说"，给出已收集到的最后一段文本，
        # 但必须让前端知道这不是正常完成的回答
        if last_text:
            warnings.append("工具调用已达轮次上限，返回的是中间内容而非最终回答")
        else:
            warnings.append("工具调用已达轮次上限，未能生成最终回答")
        result.update(answer=last_text, files=files, warnings=warnings, exhausted=True)

    def _execute_tool_call(self, tool_call: dict) -> tuple[str, bool]:
        """执行单个工具调用，返回 (内容, 是否失败)。失败内容仍回传模型保持消息序列完整。"""
        name = tool_call.get("name") or ""
        tool = self.tool_dic.get(name)
        if tool is None:
            return f"工具不存在：{name}", True
        try:
            return tool.invoke(tool_call.get("args")), False
        except Exception as exc:
            logger.warning("工具 %s 执行异常: %s", name, exc)
            return f"工具执行失败：{exc}", True

    def _ensure_initialized(self):
        """初始化与模型无关的部分：vl 客户端、工具、提示词。

        qa/agent 客户端不再在此固化——模型随请求走，见 _get_qa_llm/_get_agent_llm。
        """
        if self.vl_llm is not None:
            return

        self.vl_llm = build_chat_llm(
            model=settings.multimodal_model,
            temperature=0.7,
        )
        self.tools = [MultiplyTool(), WebSearchTool(), WordDocumentTool(), HtmlDocumentTool(), JdParserTool(), ResumeScoreTool(), ProjectRewriteTool(), MockInterviewTool(), SkillTool(), ResumeGenerateTool()]
        self.tool_dic = {tool.name: tool for tool in self.tools}

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

    def _invoke_agent(self, messages: list, query: str = "") -> tuple[str, list[dict], list[str]]:
        """执行工具轮直到拿到最终回答，同时回收本轮落盘的文件产物与告警。"""
        chain_output = ""
        files: list[dict] = []
        warnings: list[str] = []
        tool_outputs: list[tuple[str, str]] = []
        for _ in range(self._MAX_TOOL_ROUNDS):
            response = self._get_agent_llm().invoke(messages)
            messages.append(response)
            tool_calls = response.tool_calls or []
            if not tool_calls:
                chain_output = response.content
                return chain_output, files, warnings

            for tool_call in tool_calls:
                content, failed = self._execute_tool_call(tool_call)
                if failed:
                    warnings.append(f"工具 {tool_call.get('name')} 执行失败")
                tool_outputs.append((tool_call.get("name") or "", content))
                artifact = self._collect_artifact(content)
                if artifact:
                    files.append(artifact)
                messages.append(ToolMessage(tool_call_id=tool_call.get("id"), content=content))

        # 轮次用尽：与流式路径一致，先做一次不带工具的收尾调用，避免交白卷
        finalized = self._finalize_without_tools(query, tool_outputs)
        if finalized:
            warnings.append("工具调用已达轮次上限，回答由已执行工具的结果汇总生成")
            return finalized, files, warnings

        warnings.append(
            "工具调用已达轮次上限，返回的是中间内容而非最终回答"
            if chain_output
            else "工具调用已达轮次上限，未能生成最终回答"
        )
        return chain_output, files, warnings

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
            elif role == "system":
                messages.append(SystemMessage(content=content))
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
            elif role == "system":
                messages.append(SystemMessage(content=content))
        return messages