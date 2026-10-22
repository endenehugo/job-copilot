"""简历生成工具：复用 ResumeGenerationService 的结构化抽取 + 模板渲染。

为什么要有这个工具（2026-10-22）：

原来 chat 链路让模型直接调 html_document_tool 手写整份 HTML，模型要在工具参数里
吐 10~18KB 源码，实测单轮 220 秒（三次分块，每次 30~75s），期间界面完全静默。
而 ResumeGenerationService 走的是"一次 LLM 只输出约 1.5k token 的结构化 JSON
→ Jinja2 模板渲染"，同类任务实测 3.67s。

工具不向模型要 HTML，也不要一堆字段：素材从会话里取（上传的简历文档，或用户在对话中
的自述），模型只需要判断"材料够不够、要不要先追问"。
"""
import json
from typing import Any, Type

from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool


class ResumeGenerateInput(BaseModel):
    extra_info: str = Field(
        default="",
        description=(
            "用户在这次对话里补充、但可能还没写进上传文档的信息"
            "（如目标岗位、要重点突出的项目、学校名）。没有就留空字符串。"
        ),
    )


class ResumeGenerateTool(BaseTool):
    """按会话材料生成排版好的 HTML 简历（结构化抽取 + 模板渲染）。"""

    name: str = "resume_generate_tool"
    description: str = (
        "根据当前会话里的简历材料生成一份排版完整的 HTML 简历，返回下载链接与缺失字段清单。"
        "比手写 HTML 快一个数量级，生成简历时必须优先用它，不要用 html_document_tool 手写简历页面。"
        "素材来自用户上传的简历文档，或用户在对话中口述的经历。"
        "如果返回'材料不足'，先按提示向用户补齐信息，不要自己编造。"
    )
    args_schema: Type[BaseModel] = ResumeGenerateInput

    _service: Any = None

    def _run(self, extra_info: str = "") -> str:
        from app.core.model_scope import current_conversation_id
        from app.services.resume_generation_service import ResumeGenerationService

        conversation_id = current_conversation_id()
        if not conversation_id:
            return "简历生成失败：缺少会话上下文，请让用户重新发送一次请求。"

        try:
            service = ResumeGenerationService()
            extra = {"补充说明": extra_info.strip()} if extra_info and extra_info.strip() else None
            # announce=False：chat_stream 自己会写这一轮的问答消息，
            # 让 generate 再写一对会出现四个气泡
            result = service.generate(conversation_id, extra_fields=extra, announce=False)
        except ValueError as exc:
            # 素材不足是最常见的失败，原文回给模型，由它转成给用户的追问
            return f"简历生成失败：{exc}"
        except Exception as exc:  # noqa: BLE001 工具失败不能让整条流断掉
            return f"简历生成失败：{exc}"

        data = result.get("data") or {}
        payload = {
            # 供 _collect_artifact 拾取，前端渲染下载卡片
            "artifact": {
                "name": result.get("filename", ""),
                "url": result.get("html_url", ""),
                "size": len(result.get("html") or ""),
                "format": "html",
            },
            "generation_id": result.get("generation_id", ""),
            "version": result.get("version", 1),
            "target_role": data.get("target_role", ""),
            "sections": [k for k in ("education", "experiences", "projects", "skills") if data.get(k)],
            # 数字/技术栈回查原文后找不到出处的告警，模型应当在回答里如实说明
            "audit_warnings": (result.get("warnings") or [])[:5],
            "missing": result.get("missing") or [],
            "stages": result.get("stages") or {},
        }
        return json.dumps(payload, ensure_ascii=False)
