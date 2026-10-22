"""生成并保存可交付 HTML 文件的工具。

与 word_document_tool 的区别：本工具落盘 `.html` 并返回可直接写进回答的下载链接。

背景：`/make-resume` 等技能要求最终交付 HTML 文件，但模型不能只在对话里回贴 HTML 源码——
前端 `renderMarkdown` 会先过 `markdown-it({html:false})` 再过 DOMPurify，标签会被转义/剥离，
用户拿不到任何文件。所有"要给用户一个 HTML 文件"的场景都必须走本工具落盘。

为避免模型一次性吐出整份简历被截断，支持分块写入（一批块共用同一个 file_name 作为暂存键）：

    mode=once   -> 一次性写完并落盘（短文档用这个）
    mode=start  -> 建暂存文件并写入第一段
    mode=append -> 追加后续片段
    mode=finish -> 追加最后一段并落盘为正式文件

工具返回 JSON 字符串：`{"artifact": {...}, "message": "..."}`，
ConversationChatService 会解析 artifact 并把它挂到 SSE done 事件的 files 字段。
错误统一返回以"错误："开头的纯文本。
"""

from __future__ import annotations

import json
import os
import re
import threading
from html import escape
from typing import Any, Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from app.utils import ResourceUtils

_OUTPUT_DIR_NAME = "generated_docs"
_STAGING_DIR_NAME = os.path.join(_OUTPUT_DIR_NAME, ".partial")

# 文件名只允许 ASCII 字母、数字、下划线、连字符：既挡掉路径穿越，
# 也避免中文/空格文件名在下载 URL 里需要额外转义。
_FILE_STEM_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$")

_MAX_CHUNK_CHARS = 120_000
_MAX_TOTAL_CHARS = 2_000_000

_MODES = {"once", "start", "append", "finish"}

_LOCK = threading.Lock()


def _artifact_json(name: str, path: str, size: int) -> str:
    """构造带下载链接的结构化结果，供模型直接引用、供编排层抽取 files。"""
    url = f"/api/v1/conversation/file/{name}"
    payload = {
        "artifact": {
            "name": name,
            "url": url,
            "path": path,
            "size": size,
            "format": "html",
        },
        "message": (
            f"已生成 HTML 文件：{name}（{size} 字节）。"
            f"请在最终回答里用 Markdown 链接交付：[下载 {name}]({url})；"
            "不要再输出 HTML 源码，只写简短的交付说明。"
        ),
    }
    return json.dumps(payload, ensure_ascii=False)


class HtmlDocumentInput(BaseModel):
    file_name: str = Field(description="保存的 HTML 文件名，只支持字母、数字、下划线、连字符，例如 asu-resume；可不写 .html 后缀")
    html: str = Field(default="", description="HTML 内容片段。整份文档请配合 mode=once；分块时每次传入当前片段")
    title: str = Field(default="", description="文档 <title>，留空时用文件名兜底")
    mode: str = Field(default="once", description="写入模式：once（一次性写完）/ start / append / finish（分块三件套）")
    overwrite: bool = Field(default=False, description="目标文件已存在时是否覆盖；false 时自动追加 -1、-2 序号避免覆盖他人文件")


class HtmlDocumentTool(BaseTool):
    """生成 HTML 文件并返回下载链接。"""

    name: str = "html_document_tool"
    description: str = (
        "当需要交付一个 HTML 文件给用户时使用（简历、报告、海报、可视化页面等）："
        "把完整 HTML 写入 resources/generated_docs 并返回下载链接。"
        "必须先调用本工具落盘，再把链接写进回答，禁止直接把 HTML 源码贴到对话里（前端会剥掉标签）。"
        "内容较长时分块写入：mode=start 开始，mode=append 追加若干次，mode=finish 收尾落盘，"
        "同一批块必须使用相同的 file_name。"
    )
    args_schema: Type[BaseModel] = HtmlDocumentInput

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        mode = (kwargs.get("mode") or "once").strip().lower()
        if mode not in _MODES:
            return f"错误：mode 只支持 once/start/append/finish，收到 {mode!r}。"

        file_name = self._normalize_file_name(kwargs.get("file_name"))
        if not file_name:
            return (
                "错误：file_name 非法。只允许字母、数字、下划线、连字符"
                "（不含空格、中文和路径分隔符），例如 my-resume。"
            )

        html = kwargs.get("html")
        html = "" if html is None else str(html)
        title = (kwargs.get("title") or "").strip() or os.path.splitext(file_name)[0]
        overwrite = bool(kwargs.get("overwrite"))

        if mode == "once" and not html.strip():
            return "错误：mode=once 缺少 HTML 正文 html。"
        if mode == "append" and not html:
            return "错误：mode=append 未收到 html 片段。"
        if len(html) > _MAX_CHUNK_CHARS:
            return (
                f"错误：本次 html 片段过长（{len(html)} 字符，单块上限 {_MAX_CHUNK_CHARS}）。"
                "请改用 start/append/finish 分块写入。"
            )

        with _LOCK:
            if mode == "once":
                if len(html) > _MAX_TOTAL_CHARS:
                    return f"错误：HTML 总长度 {len(html)} 超过上限 {_MAX_TOTAL_CHARS}，请精简内容。"
                document = self._wrap_document(title, html)
                return self._finish(file_name, document, overwrite)

            staging_path = self._staging_path(file_name)
            if mode == "start":
                os.makedirs(os.path.dirname(staging_path), exist_ok=True)
                if os.path.exists(staging_path):
                    os.remove(staging_path)
                self._write_chunk(staging_path, html, truncate=True)
                return (
                    f"已开始写入 {file_name}（第一段 {len(html)} 字符）。"
                    "继续用相同的 file_name 以 mode=append 追加，最后用 mode=finish 收尾落盘。"
                )

            if not os.path.isfile(staging_path):
                return f"错误：找不到 {file_name} 的暂存片段，请先以 mode=start 开始，并保持 file_name 一致。"

            pending = self._read_staging(staging_path)
            if len(pending) + len(html) > _MAX_TOTAL_CHARS:
                os.remove(staging_path)
                return f"错误：累计 HTML 超过上限 {_MAX_TOTAL_CHARS} 字符，已丢弃暂存片段，请从 start 重新开始并精简内容。"

            self._write_chunk(staging_path, html, truncate=False)
            if mode == "append":
                return (
                    f"已追加 {len(html)} 字符到 {file_name}"
                    f"（累计约 {len(pending) + len(html)} 字符）。继续 append，或 finish 落盘。"
                )

            document = self._wrap_document(title, self._read_staging(staging_path))
            os.remove(staging_path)
            return self._finish(file_name, document, overwrite)

    @staticmethod
    def _normalize_file_name(file_name: Any) -> str:
        """把任意输入收敛成安全的 ASCII 文件名，不合法返回空串。"""
        raw = os.path.basename(str(file_name or "").strip().replace("\\", "/").split("/")[-1])
        if not raw:
            return ""
        stem, ext = os.path.splitext(raw)
        if ext.lower() not in {".html", ".htm"}:
            stem, ext = raw, ".html"
        if not _FILE_STEM_PATTERN.fullmatch(stem):
            return ""
        return f"{stem}{ext}"

    @staticmethod
    def _staging_path(file_name: str) -> str:
        staging_dir = ResourceUtils.ensure_resource_dir(_STAGING_DIR_NAME)
        return os.path.join(staging_dir, f"{file_name}.part")

    @staticmethod
    def _write_chunk(path: str, content: str, truncate: bool) -> None:
        mode_flag = "w" if truncate else "a"
        with open(path, mode_flag, encoding="utf-8") as handler:
            handler.write(content)

    @staticmethod
    def _read_staging(path: str) -> str:
        with open(path, "r", encoding="utf-8") as handler:
            return handler.read()

    @staticmethod
    def _wrap_document(title: str, body: str) -> str:
        """模型可能只吐 body 片段，这里补齐 HTML5 骨架并声明 UTF-8，避免中文字体乱码。"""
        head = body.lstrip()[:200].lower()
        if "<html" in head:
            return body
        return (
            "<!DOCTYPE html>\n"
            '<html lang="zh-CN">\n'
            "<head>\n"
            '<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{escape(title)}</title>\n"
            "</head>\n"
            "<body>\n"
            f"{body}\n"
            "</body>\n"
            "</html>\n"
        )

    @staticmethod
    def _finish(file_name: str, document: str, overwrite: bool) -> str:
        output_dir = ResourceUtils.ensure_resource_dir(_OUTPUT_DIR_NAME)
        target = os.path.join(output_dir, file_name)
        if not overwrite and os.path.exists(target):
            target = HtmlDocumentTool._resolve_conflict(target, output_dir, file_name)
        with open(target, "w", encoding="utf-8") as handler:
            handler.write(document)
        name = os.path.basename(target)
        # 登记归属：下载路由只拿得到文件名，不登记的话这份文件只有管理员能取。
        # conversation_id 来自请求作用域（见 core/model_scope.request_conversation_scope）
        from app.core.model_scope import current_conversation_id
        from app.services.generated_file_service import register_generated_file

        register_generated_file(name, current_conversation_id())
        return _artifact_json(name, target, os.path.getsize(target))

    @staticmethod
    def _resolve_conflict(target: str, output_dir: str, file_name: str) -> str:
        stem, ext = os.path.splitext(file_name)
        index = 1
        while index < 1000:
            candidate = os.path.join(output_dir, f"{stem}-{index}{ext}")
            if not os.path.exists(candidate):
                return candidate
            index += 1
        return target
