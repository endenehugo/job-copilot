"""HtmlDocumentTool 回归测试：分块写入、落盘、下载链接、路径安全与冲突命名。

覆盖 /make-resume 的核心交付路径——模型靠 html_document_tool 把简历落到磁盘，
任何一环出错用户就拿不到 HTML 文件。
"""

from __future__ import annotations

import json
import os


from app.tools.html_document import HtmlDocumentTool


def _artifact(result: str) -> dict:
    payload = json.loads(result)
    return payload["artifact"]


def test_once_mode_writes_document_and_returns_artifact():
    tool = HtmlDocumentTool()
    result = tool.invoke({
        "file_name": "unit-once",
        "title": "测试简历",
        "html": "<section>内容</section>",
    })

    artifact = _artifact(result)
    assert artifact["name"] == "unit-once.html"
    assert artifact["url"] == "/api/v1/conversation/file/unit-once.html"
    assert artifact["format"] == "html"
    assert os.path.isfile(artifact["path"])

    with open(artifact["path"], "r", encoding="utf-8") as handler:
        document = handler.read()
    # 片段必须被补齐成完整 HTML5 文档，且声明 UTF-8，否则中文会乱码
    assert document.startswith("<!DOCTYPE html>")
    assert '<meta charset="utf-8">' in document
    assert "<title>测试简历</title>" in document
    assert "<section>内容</section>" in document


def test_existing_html_wrapper_is_not_duplicated():
    tool = HtmlDocumentTool()
    full = "<!DOCTYPE html>\n<html lang=\"zh-CN\"><body><p>张三</p></body></html>"
    result = tool.invoke({"file_name": "unit-full", "html": full})
    with open(_artifact(result)["path"], "r", encoding="utf-8") as handler:
        assert handler.read() == full


def test_chunked_write_concatenates_all_parts():
    tool = HtmlDocumentTool()
    name = "unit-chunked"
    assert tool.invoke({"file_name": name, "mode": "start", "html": "<section>A"}) .startswith("已开始写入")
    assert tool.invoke({"file_name": name, "mode": "append", "html": "中段"}).startswith("已追加")
    result = tool.invoke({"file_name": name, "mode": "finish", "html": "</section>"})

    with open(_artifact(result)["path"], "r", encoding="utf-8") as handler:
        document = handler.read()
    assert "<section>A中段</section>" in document


def test_staging_file_is_cleaned_up_after_finish():
    tool = HtmlDocumentTool()
    tool.invoke({"file_name": "unit-cleanup", "mode": "start", "html": "A"})
    result = tool.invoke({"file_name": "unit-cleanup", "mode": "finish", "html": "B"})
    staging = os.path.join(os.path.dirname(_artifact(result)["path"]), ".partial")
    assert not os.path.exists(os.path.join(staging, "unit-cleanup.html.part"))


def test_path_traversal_is_neutralized():
    """带路径的输入会被 basename 收敛，产物必须仍在 generated_docs 内，且不改变父目录结构。"""
    tool = HtmlDocumentTool()
    for raw in ["../../evil", "/etc/passwd", "sub/dir/nested", "a/b\\c.html"]:
        result = tool.invoke({"file_name": raw, "html": "<p>x</p>"})
        artifact = _artifact(result)
        expected_dir = os.path.realpath(
            os.path.join(os.path.dirname(os.path.dirname(artifact["path"])), "")
        )
        assert os.path.realpath(artifact["path"]).startswith(expected_dir)
        assert os.path.isfile(artifact["path"])
        assert artifact["name"] == os.path.basename(artifact["name"])


def test_unsafe_file_names_are_rejected():
    """会产生非法下载 URL 的文件名必须被拒绝，而不是静默改写。"""
    tool = HtmlDocumentTool()
    for bad in ["中文简历", "with space", "", "name;.html", "name*.html"]:
        result = tool.invoke({"file_name": bad, "html": "<p>x</p>"})
        assert result.startswith("错误"), f"{bad!r} 应当被拒绝，实际返回 {result!r}"


def test_append_without_start_is_rejected():
    tool = HtmlDocumentTool()
    result = tool.invoke({"file_name": "unit-orphan", "mode": "append", "html": "x"})
    assert result.startswith("错误") and "start" in result


def test_oversized_chunk_is_rejected():
    tool = HtmlDocumentTool()
    result = tool.invoke({"file_name": "unit-huge", "html": "x" * 200_000})
    assert result.startswith("错误") and "分块" in result


def test_existing_file_is_not_clobbered_without_overwrite():
    tool = HtmlDocumentTool()
    first = _artifact(tool.invoke({"file_name": "unit-conflict", "html": "<p>first</p>"}))
    second = _artifact(tool.invoke({"file_name": "unit-conflict", "html": "<p>second</p>"}))

    assert first["name"] == "unit-conflict.html"
    assert second["name"] == "unit-conflict-1.html"
    with open(first["path"], "r", encoding="utf-8") as handler:
        assert "first" in handler.read()


def test_overwrite_replaces_same_file():
    tool = HtmlDocumentTool()
    first = tool.invoke({"file_name": "unit-overwrite", "html": "<p>first</p>"})
    second = tool.invoke({"file_name": "unit-overwrite", "html": "<p>second</p>", "overwrite": True})
    assert _artifact(first)["name"] == _artifact(second)["name"]
    with open(_artifact(second)["path"], "r", encoding="utf-8") as handler:
        body = handler.read()
    assert "second" in body and "first" not in body


def test_missing_body_in_once_mode_is_rejected():
    tool = HtmlDocumentTool()
    assert tool.invoke({"file_name": "unit-empty", "html": "   "}).startswith("错误")


def test_artifact_shape_matches_service_extractor():
    """产生的 JSON 必须能被 ConversationChatService._collect_artifact 识别。"""
    from app.services.conversation_chat_service import ConversationChatService

    tool = HtmlDocumentTool()
    result = tool.invoke({"file_name": "unit-shape", "html": "<p>x</p>"})
    artifact = ConversationChatService._collect_artifact(result)
    assert artifact is not None
    assert artifact["url"].startswith("/api/v1/conversation/file/")
    assert artifact["size"] > 0
    # 普通文本结果不应被误判成产物
    assert ConversationChatService._collect_artifact("已生成 Word 文档") is None
