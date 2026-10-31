"""会话与图片接口。

行为对齐旧版 ConversationHandler，保持契约不变：
- 错误信封的 code 为 HTTP 语义整数（400/500），HTTP 状态保持 200；
- 图片 URL 固定为 /conversation/image/<conversation_id>/<filename>——
  该格式被 ConversationChatService._parse_image_url 硬校验，且已持久化
  在历史消息的 markdown 占位符中，不能加 /api/v1 前缀或改变结构。
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import shutil
import uuid

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.deps import (
    get_conversation_chat_service,
    get_conversation_store_service,
    get_document_index_service,
)
from app.core.llm_usage import check_chat_quota
from app.core.model_policy import resolve_model
from app.core.model_scope import request_conversation_scope, request_model_scope
from app.core.ownership import (
    ADMIN_OWNER,
    assert_conversation_owner,
    get_owner_key,
    request_owner_scope,
    storage_owner_key,
)
from app.core.perf_trace import trace_scope
from app.core.response import fail, ok
from app.core.security import get_current_role
from app.services.generated_file_service import (
    delete_generated_files,
    lookup_generated_file,
    unlink_generated_files,
)
from app.schemas.conversation import (
    ChatRequest,
    CreateConversationRequest,
    DeleteConversationRequest,
    PinConversationRequest,
)
from app.utils import ResourceUtils, skill_loader

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")
image_router = APIRouter()

_SAFE_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


def _skill_error(skill: str) -> str | None:
    """校验请求里带的技能名。

    用全量清单而不是面板清单：interview / make-resume 只是不在面板里露面，
    真被显式传进来仍要按各自策略处理（见 _apply_skill），不能在这里拒掉。
    白名单的真正目的是挡住任意字符串进系统提示。
    """
    if not skill:
        return None
    valid = {item["name"] for item in skill_loader.list_skills(visible_only=False)}
    if skill not in valid:
        return f"未知技能：{skill}"
    return None


def _is_safe_segment(value: str) -> bool:
    return bool(value) and value not in {".", ".."} and bool(_SAFE_SEGMENT_PATTERN.fullmatch(value))


def _next_or_none(iterator):
    """线程池里取同步生成器的下一项。

    StopIteration 无法穿过协程边界（会被当成运行时错误），用 None 表示"流结束"。
    """
    try:
        return next(iterator)
    except StopIteration:
        return None


@router.post("/conversation/create")
def create_conversation(
    payload: CreateConversationRequest, owner_key: str = Depends(get_owner_key)
) -> dict:
    conversation = get_conversation_store_service().create_conversation(
        title=payload.title, mode=payload.mode, owner_key=storage_owner_key(owner_key)
    )
    return ok(conversation, message="创建成功")


@router.get("/conversation/list")
def list_conversations(limit: int = Query(default=50, ge=1, le=500), owner_key: str = Depends(get_owner_key)) -> dict:
    conversations = get_conversation_store_service().list_conversations(
        limit=limit, owner_key=owner_key
    )
    return ok({"conversations": conversations}, message="查询成功")


@router.get("/conversation/detail")
def conversation_detail(conversation_id: str = Query(default=""), owner_key: str = Depends(get_owner_key)) -> dict:
    conversation_id = conversation_id.strip()
    if not conversation_id:
        return fail("conversation_id 参数不能为空", code=400)
    detail = get_conversation_store_service().get_conversation_detail(
        conversation_id, owner_key=owner_key
    )
    return ok(detail, message="查询成功")


@router.post("/conversation/chat")
def conversation_chat(
    payload: ChatRequest,
    request: Request,
    response: Response,
    role: str = Depends(get_current_role),
    owner_key: str = Depends(get_owner_key),
) -> dict:
    conversation_id = payload.conversation_id.strip()
    query = payload.query.strip()
    image_urls = [item.strip() for item in payload.image_urls if item and item.strip()]
    skill = (payload.skill or "").strip()
    skill_option = (payload.skill_option or "").strip()

    if not conversation_id:
        return fail("conversation_id 参数不能为空", code=400)
    if not query and not image_urls:
        return fail("query 参数不能为空", code=400)
    skill_error = _skill_error(skill)
    if skill_error:
        return fail(skill_error, code=400)

    # 每日配额熔断（访客），管理员豁免：放在一切昂贵操作之前
    quota_error = check_chat_quota(role=role, owner_key=owner_key)
    if quota_error:
        return fail(quota_error[1], code=quota_error[0])

    try:
        with trace_scope("chat", conversation_id=conversation_id, mode=payload.mode) as trace, request_model_scope(
            payload.model, role
        ), request_conversation_scope(conversation_id), request_owner_scope(owner_key):
            result = get_conversation_chat_service().chat(
                conversation_id, query, payload.mode, image_urls, skill, skill_option
            )
    except ValueError as exc:
        return fail(str(exc), code=400)
    # Server-Timing 会暴露内部阶段耗时：服务端开关和请求头都要打开才返回
    if settings.perf_trace_header and request.headers.get("x-perf-trace") == "1":
        response.headers["Server-Timing"] = trace.to_server_timing()
    # 工具链异常结束的告警从 data 提升到信封，让前端拦截器统一提示
    warnings = result.pop("warnings", None) or []
    # 旧契约：信封 message 携带回答文本，data 携带完整结果
    return ok(result, message=result["answer"], degraded=bool(warnings), warnings=warnings)


@router.post("/conversation/chat/stream")
def conversation_chat_stream(
    payload: ChatRequest,
    role: str = Depends(get_current_role),
    owner_key: str = Depends(get_owner_key),
):
    """SSE 流式问答：meta -> delta* -> sources -> verification -> done。"""
    conversation_id = payload.conversation_id.strip()
    query = payload.query.strip()
    image_urls = [item.strip() for item in payload.image_urls if item and item.strip()]
    skill = (payload.skill or "").strip()
    skill_option = (payload.skill_option or "").strip()

    if not conversation_id:
        return fail("conversation_id 参数不能为空", code=400)
    if not query and not image_urls:
        return fail("query 参数不能为空", code=400)
    skill_error = _skill_error(skill)
    if skill_error:
        return fail(skill_error, code=400)

    # 每日配额熔断（访客）：必须在生成器启动前拒绝——流一旦开始，
    # 中途就没有"整体失败"的语义了。这里走统一信封 JSON（HTTP 200 + code 429），
    # 前端 sendStream 发现响应不是 text/event-stream 时按信封报错。
    quota_error = check_chat_quota(role=role, owner_key=owner_key)
    if quota_error:
        return fail(quota_error[1], code=quota_error[0])

    # 模型白名单先在端点内校验（快速失败），作用域必须在生成器内部开启——
    # 生成器在响应阶段才被消费，端点里 with 一下就退出时生成器还没跑
    try:
        selected_model = resolve_model(role, payload.model)
    except ValueError as exc:
        return fail(str(exc), code=400)

    def sse(event: str, data) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    async def generate():
        # 为什么不用同步生成器：starlette 的 iterate_in_threadpool 每次 __next__ 都
        # 在新 context 拷贝里执行，第一次迭代 set 的 ContextVar token 到后续迭代 reset
        # 时会抛 ValueError（Token was created in a different Context），
        # 恰好炸在 done 之后的验证阶段——用户看到的是"字打完了又冒出一段报错"。
        # 这里把整个生成器固定在同一个 context 里驱动，模型作用域在整条流上保持一致。
        iterator = get_conversation_chat_service().chat_stream(
            conversation_id, query, payload.mode, image_urls, skill, skill_option
        )
        # 计时作用域必须和模型作用域一样在生成器内部开启：端点里 with 一下就退出时，
        # 生成器还没被消费，整条流的耗时不会被记到任何 trace 上。
        with trace_scope(
            "chat_stream", conversation_id=conversation_id, mode=payload.mode
        ) as trace, request_model_scope(selected_model, role), request_conversation_scope(
            conversation_id
        ), request_owner_scope(owner_key):
            ctx = contextvars.copy_context()  # 必须在 scope 内取，才带上本次模型与会话
            try:
                while True:
                    event = await run_in_threadpool(ctx.run, _next_or_none, iterator)
                    if event is None:
                        break
                    # ttft/done 记在 trace 对象上而不是靠 ContextVar：async 生成器的
                    # 每次 __anext__ 未必在同一份 context 里，对象本身是共享的
                    if event["event"] == "delta" and "ttft_ms" not in trace.attrs:
                        trace.attrs["ttft_ms"] = round(trace.elapsed_ms(), 1)
                    elif event["event"] == "done":
                        trace.attrs["done_ms"] = round(trace.elapsed_ms(), 1)
                    yield sse(event["event"], event["data"])
            except PermissionError as exc:
                # 演示样本只读。单独分支：落到下面的 Exception 会变成"服务器内部错误"，
                # 前端就分不清"我点了只读样本"和"后端挂了"
                yield sse("error", {"message": str(exc), "code": 403})
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


@router.post("/conversation/pin")
def pin_conversation(payload: PinConversationRequest, owner_key: str = Depends(get_owner_key)) -> dict:
    """置顶 / 取消置顶（幂等）。排序由服务端负责，前端拿到后重拉列表即可。"""
    conversation_id = payload.conversation_id.strip()
    if not conversation_id:
        return fail("conversation_id 参数不能为空", code=400)
    try:
        result = get_conversation_store_service().set_conversation_pinned(
            conversation_id, payload.pinned, owner_key=owner_key
        )
    except ValueError as exc:
        return fail(str(exc), code=400)
    return ok(result, message="置顶成功" if payload.pinned else "已取消置顶")


@router.post("/conversation/delete")
def delete_conversation(payload: DeleteConversationRequest, owner_key: str = Depends(get_owner_key)) -> dict:
    """删除会话：单事务级联清理全部关联数据，事务成功后清理文件与向量索引。"""
    import shutil

    conversation_id = payload.conversation_id.strip()
    if not conversation_id:
        return fail("conversation_id 参数不能为空", code=400)

    # 越权时这里抛「会话不存在」，后面的文件清理根本不会执行——
    # 这个顺序很重要：先校验再删文件，否则别人传个别人的 ID 就能删掉一堆目录
    result = get_conversation_store_service().delete_conversation(
        conversation_id, owner_key=owner_key
    )

    for rel in (
        os.path.join("uploads", conversation_id),
        os.path.join("parsed_docs", conversation_id),
        os.path.join("faiss_index_uploads", conversation_id),
        os.path.join("uploads", "images", conversation_id),
    ):
        shutil.rmtree(ResourceUtils.get_resource_path(rel), ignore_errors=True)
    # 交付文件是散落在 generated_docs 里的，得先按登记表查出名字再逐个删，
    # 否则这个目录只增不减（旧代码就是没有这一步）
    unlink_generated_files(
        delete_generated_files(conversation_id),
        ResourceUtils.get_resource_path(_FILE_DOWNLOAD_DIR),
    )
    get_document_index_service().delete_conversation_index(conversation_id)

    return ok(result, message="删除成功")


@router.post("/conversation/image/upload")
def upload_image(
    conversation_id: str = Form(...),
    file: UploadFile = File(...),
    owner_key: str = Depends(get_owner_key),
) -> dict:
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

    get_conversation_store_service().ensure_conversation_writable(conversation_id, owner_key=owner_key)
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


_FILE_DOWNLOAD_DIR = "generated_docs"


@router.get("/conversation/file/{filename}")
def serve_generated_file(filename: str, owner_key: str = Depends(get_owner_key)):
    """下载会话过程中生成的交付文件（如 html_document_tool 产出的 HTML 简历）。

    一律以 attachment 方式下发，避免服务端托管的用户内容在本站域内被浏览器直接渲染成页面。

    归属靠生成时登记的 `generated_files` 表反查——文件名本身不含会话信息，
    没有这张表的话"知道文件名就能下载"是唯一的保护。
    """
    if not _is_safe_segment(filename):
        return fail("filename 非法", code=400)

    conversation_id = lookup_generated_file(filename)
    if conversation_id is None:
        # 未登记 = 本功能上线前的旧文件。只放行管理员，否则等于维持原来的弱保护
        if owner_key != ADMIN_OWNER:
            return fail("文件不存在", code=404)
    else:
        # 越权与不存在同样回 404，不泄漏文件名是否真实存在
        assert_conversation_owner(conversation_id, owner_key)

    file_dir = os.path.realpath(ResourceUtils.get_resource_path(_FILE_DOWNLOAD_DIR))
    file_path = os.path.realpath(os.path.join(file_dir, filename))
    if not file_path.startswith(file_dir + os.sep) or not os.path.isfile(file_path):
        return fail("文件不存在", code=404)

    return FileResponse(
        file_path,
        media_type="application/octet-stream",
        filename=filename,
        headers={"X-Content-Type-Options": "nosniff"},
    )


@image_router.get("/conversation/image/{conversation_id}/{filename}")
def serve_image(conversation_id: str, filename: str, owner_key: str = Depends(get_owner_key)):
    if not _is_safe_segment(conversation_id):
        return fail("conversation_id 非法", code=400)
    if not _is_safe_segment(filename):
        return fail("filename 非法", code=400)

    # 图片是会话资源：path 里带 conversation_id，所以能直接判归属。
    # 这个端点被 <img src> 直接引用、不带自定义头，靠同源 cookie 拿到同一份身份。
    # 只读的演示样本要能看图，所以用 assert_conversation_owner 而不是 writable。
    assert_conversation_owner(conversation_id, owner_key)

    image_dir = os.path.realpath(
        ResourceUtils.get_resource_path(os.path.join("uploads", "images", conversation_id))
    )
    file_path = os.path.realpath(os.path.join(image_dir, filename))
    # 显式目录约束，替代旧版 secure_filename 等值校验的防穿越语义
    if not file_path.startswith(image_dir + os.sep) or not os.path.isfile(file_path):
        return fail("图片不存在", code=404)
    return FileResponse(file_path)
