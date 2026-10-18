"""简历生成接口：会话内简历 + 内置 make-resume 规范 → 可编辑 A4 简历 HTML。

设计见 docs/2026-10-18-自动简历生成模块方案.md。与 /resume/project/rewrite 的区别：
那条链路只改写文字片段、不产出文件；本接口产出可直接打印/编辑的自包含 HTML。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query

from app.core.deps import get_resume_generation_service
from app.core.model_scope import request_model_scope
from app.core.response import fail, ok
from app.core.security import get_current_role
from app.schemas.resume import ResumeGenerateRequest, ResumeRegenerateRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


@router.post("/resume/generate")
def generate_resume(
    payload: ResumeGenerateRequest,
    role: str = Depends(get_current_role),
) -> dict:
    """按会话内已解析简历生成一份 A4 简历 HTML。

    返回结构化 data（可二次编辑后重新渲染）、warnings（数字/技术栈溯源告警）、
    missing（必需字段缺口）与 HTML 正文 + 下载链接。
    """
    conversation_id = (payload.conversation_id or "").strip()
    if not conversation_id:
        return fail("conversation_id 参数不能为空", code=400)

    extra = {
        str(k).strip(): str(v).strip()
        for k, v in (payload.extra_fields or {}).items()
        if str(k).strip() and str(v).strip()
    }

    try:
        with request_model_scope(payload.model, role):
            result = get_resume_generation_service().generate(
                conversation_id, extra_fields=extra or None
            )
    except ValueError as exc:
        # 包括"没有已解析简历""抽取结果无法解析""模板缺失"，都是可读的业务错误
        return fail(str(exc), code=400)
    except Exception:
        logger.exception("简历生成失败：conversation_id=%s", conversation_id)
        return fail("简历生成失败，请稍后重试", code=500)

    # html_path 是服务器本地路径，不下发给前端
    result.pop("html_path", None)
    return ok(result, message="简历已生成")


@router.get("/resume/generations")
def list_resume_generations(
    conversation_id: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    """会话内的简历版本列表（不含正文，供侧栏树与历史切换）。"""
    conversation_id = (conversation_id or "").strip()
    if not conversation_id:
        return ok({"generations": []}, message="查询成功")
    return ok(
        {"generations": get_resume_generation_service().list_generations(conversation_id, limit)},
        message="查询成功",
    )


@router.get("/resume/generations/{generation_id}")
def get_resume_generation(generation_id: str) -> dict:
    """单版详情：结构化数据 + HTML 正文 + 告警。

    HTML 文件若被清理，服务端会用 data_json 原地重渲染（响应里
    html_regenerated=true），前端不需要感知。
    """
    try:
        result = get_resume_generation_service().load_generation(generation_id)
    except ValueError as exc:
        return fail(str(exc), code=404)
    except Exception:
        logger.exception("读取简历记录失败：generation_id=%s", generation_id)
        return fail("读取简历失败，请稍后重试", code=500)
    return ok(result, message="查询成功")


@router.post("/resume/generations/{generation_id}/regenerate")
def regenerate_resume(
    generation_id: str,
    payload: ResumeRegenerateRequest,
) -> dict:
    """补字段后重渲染，产出新一版（0 次 LLM，约 20ms）。"""
    extra = {
        str(k).strip(): str(v).strip()
        for k, v in (payload.extra_fields or {}).items()
        if str(k).strip() and str(v).strip()
    }
    try:
        result = get_resume_generation_service().rerender(generation_id, extra_fields=extra or None)
    except ValueError as exc:
        return fail(str(exc), code=400)
    except Exception:
        logger.exception("简历重渲染失败：generation_id=%s", generation_id)
        return fail("简历重渲染失败，请稍后重试", code=500)
    result.pop("html_path", None)
    return ok(result, message="已生成新一版")
