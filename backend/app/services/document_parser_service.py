from __future__ import annotations

import os
import uuid
from dataclasses import dataclass

import shutil

import docx2txt
from pypdf import PdfReader

from app.core.config import settings
from app.utils import ResourceUtils


@dataclass
class DocumentParserService:
    def save_and_parse(self, conversation_id: str, storage_file) -> dict:
        """storage_file 只要求提供 .filename 与可读二进制内容，
        同时兼容 werkzeug FileStorage 与 FastAPI UploadFile。"""
        raw_filename = storage_file.filename or ""
        if not raw_filename:
            raise ValueError("缺少上传文件名")

        # 从原始文件名提取扩展名（不能用 secure_filename，它会删除中文和点号）
        extension = self._get_extension(raw_filename)
        self._validate_extension(extension)

        # original_name 保留原始文件名用于前端展示
        original_name = raw_filename

        document_id = f"doc_{uuid.uuid4().hex[:16]}"
        stored_name = f"{conversation_id}_{document_id}.{extension}"

        upload_dir = ResourceUtils.ensure_resource_dir(os.path.join("uploads", conversation_id))
        parsed_dir = ResourceUtils.ensure_resource_dir(os.path.join("parsed_docs", conversation_id))
        stored_path = os.path.join(upload_dir, stored_name)
        parsed_text_path = os.path.join(parsed_dir, f"{document_id}.txt")

        self._save_upload(storage_file, stored_path)
        try:
            content = self._parse_file(stored_path, extension)
            if not content.strip():
                raise ValueError("文档内容为空，或当前文件无法提取文本")

            with open(parsed_text_path, "w", encoding="utf-8") as parsed_file:
                parsed_file.write(content)
        except Exception:
            self._safe_delete(stored_path)
            self._safe_delete(parsed_text_path)
            raise

        return {
            "document_id": document_id,
            "original_name": original_name,
            "stored_name": stored_name,
            "stored_path": stored_path,
            "parsed_text_path": parsed_text_path,
            "file_type": extension,
            "char_count": len(content),
            "content": content,
        }

    @staticmethod
    def _get_extension(filename: str) -> str:
        return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    @staticmethod
    def _safe_delete(path: str) -> None:
        if path and os.path.exists(path):
            os.remove(path)

    @staticmethod
    def _save_upload(storage_file, stored_path: str) -> None:
        fileobj = getattr(storage_file, "file", None) or storage_file
        with open(stored_path, "wb") as output_file:
            shutil.copyfileobj(fileobj, output_file)

    def _validate_extension(self, extension: str) -> None:
        allowed_extensions = settings.upload_allowed_extensions
        if extension not in allowed_extensions:
            raise ValueError("仅支持上传 txt、pdf、docx 文件，doc 请先转换为 docx")
        if extension == "doc":
            raise ValueError("暂不支持旧版 .doc，请先另存为 .docx 后再上传")

    def _parse_file(self, file_path: str, extension: str) -> str:
        if extension == "txt":
            return self._parse_txt(file_path)
        if extension == "pdf":
            return self._parse_pdf(file_path)
        if extension == "docx":
            return self._parse_docx(file_path)
        raise ValueError(f"不支持的文件类型: {extension}")

    @staticmethod
    def _parse_txt(file_path: str) -> str:
        for encoding in ("utf-8", "gbk"):
            try:
                with open(file_path, "r", encoding=encoding) as text_file:
                    return text_file.read()
            except UnicodeDecodeError:
                continue
        raise ValueError("TXT 文件编码无法识别，请使用 UTF-8 或 GBK")

    @staticmethod
    def _parse_pdf(file_path: str) -> str:
        reader = PdfReader(file_path)
        texts = []
        for page in reader.pages:
            texts.append(page.extract_text() or "")
        content = "\n".join(texts).strip()
        if not content:
            raise ValueError("PDF 未提取到有效文本，可能是扫描件或空白页")
        return content

    @staticmethod
    def _parse_docx(file_path: str) -> str:
        content = docx2txt.process(file_path) or ""
        content = content.strip()
        if not content:
            raise ValueError("DOCX 未提取到有效文本")
        return content