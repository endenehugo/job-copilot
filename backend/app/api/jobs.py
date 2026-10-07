"""JD 解析与简历评分接口。

行为对齐旧版 JobHandler + ImageHandler.analyze_screenshot；
分析落库统一走 job_analysis_store_service.save_analysis
（合并旧版两份重复实现，并修复 image 链路漏更新简历版本分数的问题）。
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Query

from app.core.deps import (
    get_conversation_store_service,
    get_image_analysis_service,
    get_job_description_service,
    get_resume_scoring_service,
)
from app.core.response import fail, ok
from app.repositories import DatabaseManager, JobAnalysisRepository
from app.schemas.job import JobAnalyzeRequest, ScreenshotAnalyzeRequest
from app.services.job_analysis_store_service import (
    analysis_entity_to_dict,
    build_analysis_response,
    save_analysis,
)

router = APIRouter(prefix="/api/v1")


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
def analyze_job(payload: JobAnalyzeRequest) -> dict:
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

    jd_analysis = get_job_description_service().analyze(jd_text)
    scoring_result = get_resume_scoring_service().score(resume_text, jd_text, jd_analysis)
    save_analysis(
        conversation_id=conversation_id,
        jd_text=jd_text,
        jd_analysis=jd_analysis,
        scoring_result=scoring_result,
    )
    return ok(build_analysis_response(jd_analysis, scoring_result), message="分析完成")


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


@router.post("/job/analyze-from-screenshot")
def analyze_from_screenshot(payload: ScreenshotAnalyzeRequest) -> dict:
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

    # 3-4. 解析 JD 并评分
    jd_analysis = get_job_description_service().analyze(jd_text)
    scoring_result = get_resume_scoring_service().score(resume_text, jd_text, jd_analysis)

    # 5. 落库
    save_analysis(
        conversation_id=conversation_id,
        jd_text=jd_text,
        jd_analysis=jd_analysis,
        scoring_result=scoring_result,
    )

    # 6. 组装返回
    response_data = build_analysis_response(jd_analysis, scoring_result)
    response_data["extracted_jd_preview"] = jd_text[:500] + ("..." if len(jd_text) > 500 else "")
    return ok(response_data, message="截图分析完成")
