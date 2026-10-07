"""结果导出接口（Markdown）。行为对齐旧版 ExportHandler。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.deps import get_export_service
from app.core.response import fail, ok
from app.schemas.export import ExportProjectRewriteRequest

router = APIRouter(prefix="/api/v1")


@router.get("/export/analysis")
def export_analysis(analysis_id: int = Query(default=0)) -> dict:
    if not analysis_id:
        return fail("analysis_id 参数不能为空", code=400)

    markdown = get_export_service().export_analysis_markdown(analysis_id)
    return ok({"markdown": markdown, "format": "markdown"})


@router.get("/export/interview")
def export_interview(session_id: str = Query(default="")) -> dict:
    session_id = session_id.strip()
    if not session_id:
        return fail("session_id 参数不能为空", code=400)

    markdown = get_export_service().export_interview_markdown(session_id)
    return ok({"markdown": markdown, "format": "markdown"})


@router.post("/export/project-rewrite")
def export_project_rewrite(payload: ExportProjectRewriteRequest) -> dict:
    if not payload.result:
        return fail("result 参数不能为空", code=400)

    markdown = get_export_service().export_project_rewrite_markdown(payload.result)
    return ok({"markdown": markdown, "format": "markdown"})
