"""会话与图片接口。

行为对齐旧版 ConversationHandler，保持契约不变：
- 错误信封的 code 为 HTTP 语义整数（400/500），HTTP 状态保持 200；
- 图片 URL 固定为 /conversation/image/<conversation_id>/<filename>——
  该格式被 ConversationChatService._parse_image_url 硬校验，且已持久化
  在历史消息的 markdown 占位符中，不能加 /api/v1 前缀或改变结构。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import uuid

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from app.core.config import settings
from app.core.deps import (
    get_conversation_chat_service,
    get_conversation_store_service,
    get_document_index_service,
)
from app.core.response import fail, ok
from app.schemas.conversation import ChatRequest, CreateConversationRequest, DeleteConversationRequest
from app.utils import ResourceUtils

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")
image_router = APIRouter()

_SAFE_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


def _is_safe_segment(value: str) -> bool:
    return bool(value) and value not in {".", ".."} and bool(_SAFE_SEGMENT_PATTERN.fullmatch(value))


@router.post("/conversation/create")
def create_conversation(payload: CreateConversationRequest) -> dict:
    conversation = get_conversation_store_service().create_conversation(
        title=payload.title, mode=payload.mode
    )
    return ok(conversation, message="创建成功")


@router.get("/conversation/list")
def list_conversations(limit: int = Query(default=50, ge=1, le=500)) -> dict:
    conversations = get_conversation_store_service().list_conversations(limit=limit)
    return ok({"conversations": conversations}, message="查询成功")


@router.get("/conversation/detail")
def conversation_detail(conversation_id: str = Query(default="")) -> dict:
    conversation_id = conversation_id.strip()
    if not conversation_id:
        return fail("conversation_id 参数不能为空", code=400)
    detail = get_conversation_store_service().get_conversation_detail(conversation_id)
    return ok(detail, message="查询成功")


@router.post("/conversation/chat")
def conversation_chat(payload: ChatRequest) -> dict:
    conversation_id = payload.conversation_id.strip()
    query = payload.query.strip()
    image_urls = [item.strip() for item in payload.image_urls if item and item.strip()]

    if not conversation_id:
        return fail("conversation_id 参数不能为空", code=400)
    if not query and not image_urls:
        return fail("query 参数不能为空", code=400)

    result = get_conversation_chat_service().chat(
        conversation_id, query, payload.mode, image_urls
    )
    # 旧契约：信封 message 携带回答文本，data 携带完整结果
    return ok(result, message=result["answer"])


@router.post("/conversation/chat/stream")
def conversation_chat_stream(payload: ChatRequest):
    """SSE 流式问答：meta -> delta* -> sources -> verification -> done。"""
    conversation_id = payload.conversation_id.strip()
    query = payload.query.strip()
    image_urls = [item.strip() for item in payload.image_urls if item and item.strip()]

    if not conversation_id:
        return fail("conversation_id 参数不能为空", code=400)
    if not query and not image_urls:
        return fail("query 参数不能为空", code=400)

    def sse(event: str, data) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    def generate():
        try:
            for event in get_conversation_chat_service().chat_stream(
                conversation_id, query, payload.mode, image_urls
            ):
                yield sse(event["event"], event["data"])
        except ValueError as exc:
            yield sse("error", {"message": str(exc)})
        except Exception as exc:
            logger.exception("流式问答异常: %s", exc)
            yield sse("error", {"message": f"服务器内部错误: {exc}"})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Nginx 反代时禁用缓冲，保证打字机效果
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/conversation/delete")
def delete_conversation(payload: DeleteConversationRequest) -> dict:
    """删除会话：单事务级联清理全部关联数据，事务成功后清理文件与向量索引。"""
    import shutil

    conversation_id = payload.conversation_id.strip()
    if not conversation_id:
        return fail("conversation_id 参数不能为空", code=400)

    result = get_conversation_store_service().delete_conversation(conversation_id)

    for rel in (
        os.path.join("uploads", conversation_id),
        os.path.join("parsed_docs", conversation_id),
        os.path.join("faiss_index_uploads", conversation_id),
        os.path.join("uploads", "images", conversation_id),
    ):
        shutil.rmtree(ResourceUtils.get_resource_path(rel), ignore_errors=True)
    get_document_index_service().delete_conversation_index(conversation_id)

    return ok(result, message="删除成功")


@router.post("/conversation/image/upload")
def upload_image(conversation_id: str = Form(...), file: UploadFile = File(...)) -> dict:
    conversation_id = conversation_id.strip()
    original_filename = file.filename or ""
    if not conversation_id:
        return fail("conversation_id 参数不能为空", code=400)
    if not _is_safe_segment(conversation_id):
        return fail("conversation_id 非法", code=400)
    if not original_filename.strip():
        return fail("文件名不能为空", code=400)

    extension = os.path.splitext(original_filename)[1].lower().lstrip(".")
    if extension not in settings.image_allowed_extensions:
        return fail("仅支持上传 png、jpg、jpeg、webp 格式的图片", code=400)
    if not (file.content_type or "").startswith("image/"):
        return fail("上传文件不是合法图片", code=400)
    if file.size and file.size > settings.upload_max_content_length:
        return fail("文件大小超过上传限制", code=400)

    get_conversation_store_service().ensure_conversation_exists(conversation_id)
    save_dir = ResourceUtils.ensure_resource_dir(os.path.join("uploads", "images", conversation_id))
    stored_name = f"img_{uuid.uuid4().hex[:12]}.{extension}"
    save_path = os.path.join(save_dir, stored_name)
    with open(save_path, "wb") as output_file:
        shutil.copyfileobj(file.file, output_file)

    image_url = f"/conversation/image/{conversation_id}/{stored_name}"
    return ok(
        {
            "image_url": image_url,
            "filename": stored_name,
            "mime_type": file.content_type or f"image/{extension}",
        },
        message="图片上传成功",
    )


@image_router.get("/conversation/image/{conversation_id}/{filename}")
def serve_image(conversation_id: str, filename: str):
    if not _is_safe_segment(conversation_id):
        return fail("conversation_id 非法", code=400)
    if not _is_safe_segment(filename):
        return fail("filename 非法", code=400)

    image_dir = os.path.realpath(
        ResourceUtils.get_resource_path(os.path.join("uploads", "images", conversation_id))
    )
    file_path = os.path.realpath(os.path.join(image_dir, filename))
    # 显式目录约束，替代旧版 secure_filename 等值校验的防穿越语义
    if not file_path.startswith(image_dir + os.sep) or not os.path.isfile(file_path):
        return fail("图片不存在", code=404)
    return FileResponse(file_path)
