"""JD 解析与简历评分接口。

行为对齐旧版 JobHandler + ImageHandler.analyze_screenshot；
分析落库统一走 job_analysis_store_service.save_analysis
（合并旧版两份重复实现，并修复 image 链路漏更新简历版本分数的问题）。
"""

from __future__ import annotations

import json
import logging
import os

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.core.deps import (
    get_conversation_store_service,
    get_image_analysis_service,
    get_job_description_service,
    get_resume_scoring_service,
)
from app.core.model_scope import request_model_scope
from app.core.response import fail, ok
from app.core.security import get_current_role
from app.repositories import DatabaseManager, JobAnalysisRepository
from app.schemas.job import (
    JobAnalysisDeleteRequest,
    JobAnalyzeRequest,
    JobAnalyzeStreamRequest,
    ScreenshotAnalyzeRequest,
)
from app.services.job_analysis_store_service import (
    analysis_entity_to_dict,
    build_analysis_response,
    save_analysis,
)

router = APIRouter(prefix="/api/v1")
logger = logging.getLogger(__name__)


def _read_latest_resume_text(conversation_id: str) -> str:
    """读取会话中最新上传简历的解析文本。"""
    documents = get_conversation_store_service().get_conversation_documents(conversation_id)
    if not documents:
        return ""
    latest_doc = documents[-1]
    parsed_text_path = latest_doc.get("parsed_text_path")
    if parsed_text_path and os.path.exists(parsed_text_path):
        with open(parsed_text_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""


@router.post("/job/analyze")
def analyze_job(payload: JobAnalyzeRequest, role: str = Depends(get_current_role)) -> dict:
    conversation_id = payload.conversation_id.strip()
    jd_text = payload.jd_text.strip()

    if not conversation_id:
        return fail("conversation_id 参数不能为空", code=400)
    if not jd_text:
        return fail("jd_text 参数不能为空", code=400)

    store_service = get_conversation_store_service()
    store_service.ensure_conversation_exists(conversation_id)

    resume_text = _read_latest_resume_text(conversation_id)
    if not resume_text:
        documents = store_service.get_conversation_documents(conversation_id)
        if not documents:
            return fail("当前会话未上传简历，请先上传简历", code=400)
        return fail("无法读取简历解析文本", code=500)

    try:
        with request_model_scope(payload.model, role):
            jd_analysis = get_job_description_service().analyze(jd_text)
            scoring_result = get_resume_scoring_service().score(resume_text, jd_text, jd_analysis)
    except ValueError as exc:
        return fail(str(exc), code=400)
    analysis_id = save_analysis(
        conversation_id=conversation_id,
        jd_text=jd_text,
        jd_analysis=jd_analysis,
        scoring_result=scoring_result,
    )
    response_data = build_analysis_response(jd_analysis, scoring_result)
    response_data["analysis_id"] = analysis_id
    warnings = scoring_result.get("warnings") or []
    return ok(response_data, message="分析完成", degraded=bool(warnings), warnings=warnings)


@router.post("/job/analyze/stream")
async def analyze_job_stream(payload: JobAnalyzeStreamRequest, role: str = Depends(get_current_role)):
    """JD 分析（SSE）：按阶段推送进度，最后一条 done 携带与非流式端点一致的完整结果。

    为什么需要它：一次分析是两次模型调用（JD 解析 + 匹配评分），实测 8 秒起步、
    模型排队时更久，前端原来只有一个"分析中"的按钮状态——用户分不清是在跑还是卡死。
    这里把阶段推给前端，进度条才有真实依据，而不是按时间瞎涨。

    事件：stage（阶段+百分比）*  ->  done（完整结果）| error（message）
    """
    conversation_id = payload.conversation_id.strip()
    jd_text = payload.jd_text.strip()
    image_url = payload.image_url.strip()

    if not conversation_id:
        return fail("conversation_id 参数不能为空", code=400)
    if not jd_text and not image_url:
        return fail("jd_text 或 image_url 至少提供一个", code=400)

    # 前置校验放在流开始之前：这样"没简历/图片不存在"还能按普通 400 信封返回，
    # 前端不必从 SSE 里解析错误
    store_service = get_conversation_store_service()
    store_service.ensure_conversation_exists(conversation_id)
    resume_text = _read_latest_resume_text(conversation_id)
    if not resume_text:
        documents = store_service.get_conversation_documents(conversation_id)
        if not documents:
            return fail("当前会话未上传简历，请先上传简历", code=400)
        return fail("无法读取简历解析文本", code=500)

    image_path = ""
    if image_url:
        from app.services.conversation_chat_service import ConversationChatService
        from app.utils import ResourceUtils

        try:
            conv_id, filename = ConversationChatService._parse_image_url(image_url)
        except ValueError as exc:
            return fail(f"图片地址非法: {exc}", code=400)
        image_path = os.path.join(
            ResourceUtils.get_resource_path(os.path.join("uploads", "images", conv_id)), filename
        )
        if not os.path.isfile(image_path):
            return fail("图片文件不存在", code=400)

    def sse(event: str, data) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    async def generate():
        # 模型作用域必须在生成器内部开：端点在返回 StreamingResponse 后就退出了，
        # 在这里 with 一下等于没生效。run_in_threadpool 会拷贝当前 context，
        # 所以每段的模型调用都能读到本次选型。
        try:
            with request_model_scope(payload.model, role):
                text = jd_text
                if image_path:
                    yield sse("stage", {"stage": "vision", "label": "识别截图中的 JD", "percent": 12})
                    text = await run_in_threadpool(
                        get_image_analysis_service().extract_jd_from_screenshot, image_path
                    )
                    text = (text or "").strip()
                    if len(text) < 20:
                        yield sse("error", {"message": "无法从截图识别出有效的 JD 文本，请确认图片清晰度"})
                        return

                yield sse(
                    "stage",
                    {"stage": "jd_parse", "label": "解析 JD 结构", "percent": 35 if image_path else 18},
                )
                jd_analysis = await run_in_threadpool(get_job_description_service().analyze, text)

                yield sse("stage", {"stage": "scoring", "label": "匹配简历并评分", "percent": 68})
                scoring_result = await run_in_threadpool(
                    get_resume_scoring_service().score, resume_text, text, jd_analysis
                )

                yield sse("stage", {"stage": "save", "label": "保存分析结果", "percent": 93})
                analysis_id = await run_in_threadpool(
                    save_analysis,
                    conversation_id=conversation_id,
                    jd_text=text,
                    jd_analysis=jd_analysis,
                    scoring_result=scoring_result,
                )
                data = build_analysis_response(jd_analysis, scoring_result)
                data["analysis_id"] = analysis_id
                # 截图链路的 JD 是识别出来的，带回去用户才知道分析的是哪段文本
                if image_path:
                    data["jd_text"] = text
                warnings = scoring_result.get("warnings") or []
                if warnings:
                    data["warnings"] = warnings
                yield sse("done", data)
        except ValueError as exc:
            yield sse("error", {"message": str(exc)})
        except Exception as exc:
            logger.exception("JD 分析流式异常: %s", exc)
            yield sse("error", {"message": f"分析失败: {exc}"})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/job/analysis/latest")
def latest_job_analysis(conversation_id: str = Query(default="")) -> dict:
    conversation_id = conversation_id.strip()
    if not conversation_id:
        return fail("conversation_id 参数不能为空", code=400)

    session = DatabaseManager.get_session()
    try:
        analysis = JobAnalysisRepository.get_latest_by_conversation_id(session, conversation_id)
        if analysis is None:
            return fail("暂无分析记录", code=404)
        return ok(analysis_entity_to_dict(analysis))
    finally:
        DatabaseManager.remove_session()


@router.get("/job/analysis/list")
def list_job_analyses(conversation_id: str = Query(default="")) -> dict:
    conversation_id = conversation_id.strip()
    if not conversation_id:
        return fail("conversation_id 参数不能为空", code=400)

    session = DatabaseManager.get_session()
    try:
        analysis_list = JobAnalysisRepository.list_by_conversation_id(session, conversation_id)
        return ok({"analyses": [analysis_entity_to_dict(item) for item in analysis_list]})
    finally:
        DatabaseManager.remove_session()


@router.get("/job/analysis/detail")
def job_analysis_detail(analysis_id: int = Query(default=0)) -> dict:
    """单条分析全量回看（会话树子节点用），结构同 /job/analysis/latest。"""
    if analysis_id <= 0:
        return fail("analysis_id 参数不能为空", code=400)

    session = DatabaseManager.get_session()
    try:
        analysis = JobAnalysisRepository.get_by_id(session, analysis_id)
        if analysis is None:
            return fail("分析记录不存在", code=404)
        data = analysis_entity_to_dict(analysis)
        # 回看时带上 JD 原文，主区视图可展示分析依据
        data["jd_text"] = analysis.jd_text
        return ok(data)
    finally:
        DatabaseManager.remove_session()


@router.post("/job/analysis/delete")
def delete_job_analysis(payload: JobAnalysisDeleteRequest) -> dict:
    """删除单条分析记录，供会话树子节点操作。

    删除策略（2026-10-13 评审定稿）：允许删除并由前端确认提示；
    注意面试链路会实时读取"最新一条分析"的 jd_text，删除后该会话
    面试追问将拿不到 JD，前端提示需覆盖这一点。
    """
    if payload.analysis_id <= 0:
        return fail("analysis_id 参数不能为空", code=400)

    session = DatabaseManager.get_session()
    try:
        with session.begin():
            analysis = JobAnalysisRepository.get_by_id(session, payload.analysis_id)
            if analysis is None:
                return fail("分析记录不存在", code=404)
            session.delete(analysis)
    finally:
        DatabaseManager.remove_session()
    return ok({"analysis_id": payload.analysis_id}, message="分析记录已删除")


@router.post("/job/analyze-from-screenshot")
def analyze_from_screenshot(payload: ScreenshotAnalyzeRequest, role: str = Depends(get_current_role)) -> dict:
    conversation_id = payload.conversation_id.strip()
    image_url = payload.image_url.strip()

    if not conversation_id:
        return fail("conversation_id 参数不能为空", code=400)
    if not image_url:
        return fail("image_url 参数不能为空", code=400)

    store_service = get_conversation_store_service()
    store_service.ensure_conversation_exists(conversation_id)

    # 解析图片 URL 为本地路径（复用对话服务里的白名单校验）
    from app.services.conversation_chat_service import ConversationChatService
    from app.utils import ResourceUtils

    try:
        conv_id, filename = ConversationChatService._parse_image_url(image_url)
    except ValueError as exc:
        return fail(f"图片地址非法: {exc}", code=400)

    image_dir = ResourceUtils.get_resource_path(os.path.join("uploads", "images", conv_id))
    image_path = os.path.join(image_dir, filename)
    if not os.path.isfile(image_path):
        return fail("图片文件不存在", code=400)

    # 1. 从截图中提取 JD 文本
    jd_text = get_image_analysis_service().extract_jd_from_screenshot(image_path)
    if not jd_text or len(jd_text.strip()) < 20:
        return fail("无法从截图识别出有效的 JD 文本，请确认图片清晰度", code=500)

    # 2. 获取简历
    resume_text = _read_latest_resume_text(conversation_id)
    if not resume_text:
        documents = store_service.get_conversation_documents(conversation_id)
        if not documents:
            return fail("当前会话未上传简历，请先上传简历", code=400)
        return fail("无法读取简历解析文本", code=500)

    # 3-4. 解析 JD 并评分（vl 提取文本不走用户所选模型，主链路走）
    try:
        with request_model_scope(payload.model, role):
            jd_analysis = get_job_description_service().analyze(jd_text)
            scoring_result = get_resume_scoring_service().score(resume_text, jd_text, jd_analysis)
    except ValueError as exc:
        return fail(str(exc), code=400)

    # 5. 落库
    analysis_id = save_analysis(
        conversation_id=conversation_id,
        jd_text=jd_text,
        jd_analysis=jd_analysis,
        scoring_result=scoring_result,
    )

    # 6. 组装返回
    response_data = build_analysis_response(jd_analysis, scoring_result)
    response_data["analysis_id"] = analysis_id
    response_data["extracted_jd_preview"] = jd_text[:500] + ("..." if len(jd_text) > 500 else "")
    warnings = scoring_result.get("warnings") or []
    return ok(response_data, message="截图分析完成", degraded=bool(warnings), warnings=warnings)
