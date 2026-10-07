import os
import sys
from pathlib import Path

from docx import Document

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    from app.core.config import settings  # noqa: F401  # 触发配置加载与资源路径初始化
    from app.tools.word_document import WordDocumentTool

    tool = WordDocumentTool()
    result = tool.invoke(
        {
            "file_name": "test_word_document_tool.docx",
            "title": "测试标题",
            "content": "第一段内容\n第二段内容",
        }
    )

    print("工具返回结果:")
    print(result)

    if "已生成 Word 文档" not in result:
        raise AssertionError("工具未返回成功消息")

    output_path = result.split("保存路径：", 1)[-1].strip()
    if not os.path.exists(output_path):
        raise AssertionError(f"未找到生成的文档: {output_path}")

    document = Document(output_path)
    paragraphs = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]

    if not paragraphs or paragraphs[0] != "测试标题":
        raise AssertionError(f"文档标题不正确: {paragraphs}")

    if paragraphs[1:] != ["第一段内容", "第二段内容"]:
        raise AssertionError(f"文档正文不正确: {paragraphs}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
