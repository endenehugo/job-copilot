"""文档上传与删除接口。

编排逻辑与旧版 DocumentHandler 一致：保存解析 → 绑定（parsed）→ 重建会话索引
→ 置为 indexed（失败置 failed 并重新抛出）→ 创建简历版本记录。
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime

from fastapi import APIRouter, File, Form, UploadFile

from app.core.config import settings
from app.core.deps import (
    get_conversation_store_service,
    get_document_index_service,
    get_document_parser_service,
)
from app.core.response import fail, ok
from app.repositories import DatabaseManager, ResumeVersionRepository
from app.schemas.document import DeleteDocumentRequest

router = APIRouter(prefix="/api/v1")


@router.post("/document/upload")
def upload_document(conversation_id: str = Form(...), file: UploadFile = File(...)) -> dict:
    conversation_id = conversation_id.strip()
    if not conversation_id:
        return fail("conversation_id 参数不能为空", code=400)
    if not (file.filename or "").strip():
        return fail("缺少上传文件", code=400)
    if file.size and file.size > settings.upload_max_content_length:
        return fail("文件大小超过上传限制", code=400)

    store_service = get_conversation_store_service()
    store_service.ensure_conversation_exists(conversation_id)

    parsed_document = get_document_parser_service().save_and_parse(conversation_id, file)
    document = store_service.bind_document(conversation_id, parsed_document, status="parsed")
    try:
        documents = store_service.get_conversation_documents(conversation_id)
        get_document_index_service().rebuild_conversation_index(conversation_id, documents)
        document = store_service.update_document_status(document["document_id"], "indexed")
    except Exception:
        store_service.update_document_status(document["document_id"], "failed")
        raise

    _create_resume_version(conversation_id, document, parsed_document)
    return ok({"document": document}, message="上传成功")


@router.post("/document/delete")
def delete_document(payload: DeleteDocumentRequest) -> dict:
    document_id = payload.document_id.strip()
    if not document_id:
        return fail("document_id 参数不能为空", code=400)

    store_service = get_conversation_store_service()
    document = store_service.remove_document(document_id)
    _safe_delete(document.get("stored_path"))
    _safe_delete(document.get("parsed_text_path"))

    remaining_documents = store_service.get_conversation_documents(document["conversation_id"])
    if remaining_documents:
        get_document_index_service().rebuild_conversation_index(
            document["conversation_id"], remaining_documents
        )
    else:
        get_document_index_service().delete_conversation_index(document["conversation_id"])

    return ok({"document_id": document_id}, message="删除成功")


def _safe_delete(path: str | None) -> None:
    if path and os.path.exists(path):
        os.remove(path)


def _create_resume_version(conversation_id: str, document: dict, parsed_document: dict) -> None:
    """创建简历版本记录。

    读取 max_version_number 与写入必须在同一个事务里：SQLAlchemy 2.0 下
    先 execute()（自动开启事务）再显式 begin() 会抛
    InvalidRequestError("A transaction is already begun on this Session")，
    旧版 handler 正是这个写法，导致上传接口在索引成功后仍返回 500。
    """
    session = DatabaseManager.get_session()
    try:
        with session.begin():
            max_ver = ResumeVersionRepository.get_max_version_number(session, conversation_id)
            ResumeVersionRepository.create(
                session,
                version_id=f"rv_{uuid.uuid4().hex[:12]}",
                conversation_id=conversation_id,
                document_id=document["document_id"],
                version_number=max_ver + 1,
                original_name=document.get("original_name", ""),
                char_count=parsed_document.get("char_count", 0),
                created_at=datetime.now(),
            )
    finally:
        DatabaseManager.remove_session()
