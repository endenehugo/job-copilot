"""结果导出接口（Markdown）。行为对齐旧版 ExportHandler。

这里的端点都不经过 conversation_id：`analysis_id` 是自增整数、
`session_id` 也能被枚举猜。这是典型的"按子表 id 直取"路径，
必须回查父会话归属，否则前面两层授权等于没做。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.deps import get_export_service
from app.core.ownership import assert_child_owner, get_owner_key
from app.core.response import fail, ok
from app.repositories import (
    DatabaseManager,
    InterviewSessionRepository,
    JobAnalysisRepository,
)
from app.schemas.export import ExportProjectRewriteRequest

router = APIRouter(prefix="/api/v1")


@router.get("/export/analysis")
def export_analysis(
    analysis_id: int = Query(default=0), owner_key: str = Depends(get_owner_key)
) -> dict:
    if not analysis_id:
        return fail("analysis_id 参数不能为空", code=400)

    session = DatabaseManager.get_session()
    try:
        analysis = JobAnalysisRepository.get_by_id(session, analysis_id)
        if analysis is None:
            return fail("分析记录不存在", code=404)
        assert_child_owner(session, analysis.conversation_id, owner_key, "分析记录不存在")
    finally:
        DatabaseManager.remove_session()

    markdown = get_export_service().export_analysis_markdown(analysis_id)
    return ok({"markdown": markdown, "format": "markdown"})


@router.get("/export/interview")
def export_interview(
    session_id: str = Query(default=""), owner_key: str = Depends(get_owner_key)
) -> dict:
    session_id = session_id.strip()
    if not session_id:
        return fail("session_id 参数不能为空", code=400)

    session = DatabaseManager.get_session()
    try:
        interview_session = InterviewSessionRepository.get_by_session_id(session, session_id)
        if interview_session is None:
            return fail("面试会话不存在", code=404)
        assert_child_owner(session, interview_session.conversation_id, owner_key, "面试会话不存在")
    finally:
        DatabaseManager.remove_session()

    markdown = get_export_service().export_interview_markdown(session_id)
    return ok({"markdown": markdown, "format": "markdown"})


@router.post("/export/project-rewrite")
def export_project_rewrite(payload: ExportProjectRewriteRequest) -> dict:
    """纯格式转换：入参就是结果本身，不读库，不涉及归属。"""
    if not payload.result:
        return fail("result 参数不能为空", code=400)

    markdown = get_export_service().export_project_rewrite_markdown(payload.result)
    return ok({"markdown": markdown, "format": "markdown"})
