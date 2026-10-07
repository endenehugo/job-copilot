"""简历接口：项目经历优化 + 版本管理。行为对齐旧版 Project/ResumeHandler。"""

from __future__ import annotations

import os

from fastapi import APIRouter, Query

from app.core.deps import (
    get_conversation_store_service,
    get_project_rewrite_service,
)
from app.core.response import fail, ok
from app.repositories import DatabaseManager, ResumeVersionRepository
from app.schemas.job import ProjectRewriteRequest

router = APIRouter(prefix="/api/v1")


@router.post("/resume/project/rewrite")
def rewrite_project(payload: ProjectRewriteRequest) -> dict:
    """优化项目经历描述（行为对齐旧版 ProjectHandler.rewrite）。"""
    conversation_id = payload.conversation_id.strip()
    project_description = payload.project_description.strip()

    if not conversation_id:
        return fail("conversation_id 参数不能为空", code=400)
    if not project_description:
        return fail("project_description 参数不能为空", code=400)

    store_service = get_conversation_store_service()
    store_service.ensure_conversation_exists(conversation_id)

    # 收集简历上下文（可选，取前 2000 字帮助更精准的优化）
    context = None
    documents = store_service.get_conversation_documents(conversation_id)
    if documents:
        latest_doc = documents[-1]
        parsed_text_path = latest_doc.get("parsed_text_path")
        if parsed_text_path and os.path.exists(parsed_text_path):
            with open(parsed_text_path, "r", encoding="utf-8") as f:
                resume_text = f.read().strip()
            if resume_text:
                context = resume_text[:2000]

    result = get_project_rewrite_service().rewrite(project_description, context)
    return ok(result, message="优化完成")


def _version_to_dict(v) -> dict:
    return {
        "version_id": v.version_id,
        "document_id": v.document_id,
        "version_number": v.version_number,
        "original_name": v.original_name,
        "char_count": v.char_count,
        "total_score": v.total_score,
        "dimensions": (
            {
                "skill_match": v.skill_match_score,
                "project_relevance": v.project_relevance_score,
                "expression_quality": v.expression_quality_score,
                "job_fitness": v.job_fitness_score,
            }
            if v.total_score is not None
            else None
        ),
        "created_at": v.created_at.isoformat() if v.created_at else "",
    }


@router.get("/resume/versions/list")
def list_resume_versions(conversation_id: str = Query(default="")) -> dict:
    conversation_id = conversation_id.strip()
    if not conversation_id:
        return fail("conversation_id 参数不能为空", code=400)

    session = DatabaseManager.get_session()
    try:
        versions = ResumeVersionRepository.list_by_conversation_id(session, conversation_id)
        return ok({"versions": [_version_to_dict(v) for v in versions]})
    finally:
        DatabaseManager.remove_session()


@router.get("/resume/versions/detail")
def resume_version_detail(version_id: str = Query(default="")) -> dict:
    version_id = version_id.strip()
    if not version_id:
        return fail("version_id 参数不能为空", code=400)

    session = DatabaseManager.get_session()
    try:
        version = ResumeVersionRepository.get_by_version_id(session, version_id)
        if version is None:
            return fail("版本不存在", code=404)
        return ok(_version_to_dict(version))
    finally:
        DatabaseManager.remove_session()


@router.get("/resume/versions/compare")
def compare_resume_versions(conversation_id: str = Query(default="")) -> dict:
    conversation_id = conversation_id.strip()
    if not conversation_id:
        return fail("conversation_id 参数不能为空", code=400)

    session = DatabaseManager.get_session()
    try:
        versions = ResumeVersionRepository.list_by_conversation_id(session, conversation_id)
        scored = [v for v in versions if v.total_score is not None]
        scored.sort(key=lambda v: v.version_number)

        score_history = [
            {
                "version_number": v.version_number,
                "original_name": v.original_name,
                "total_score": v.total_score,
                "skill_match": v.skill_match_score,
                "project_relevance": v.project_relevance_score,
                "expression_quality": v.expression_quality_score,
                "job_fitness": v.job_fitness_score,
                "created_at": v.created_at.isoformat() if v.created_at else "",
            }
            for v in scored
        ]
        return ok({"score_history": score_history, "trend": _calc_trend(score_history)})
    finally:
        DatabaseManager.remove_session()


def _calc_trend(score_history: list[dict]) -> str:
    """计算分数趋势（与旧版一致）。"""
    if len(score_history) < 2:
        return "stable"
    first = score_history[0].get("total_score", 0) or 0
    last = score_history[-1].get("total_score", 0) or 0
    if last - first > 5:
        return "up"
    if first - last > 5:
        return "down"
    return "stable"
