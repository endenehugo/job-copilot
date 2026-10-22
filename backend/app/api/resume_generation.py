"""简历生成接口：会话内简历 + 内置 make-resume 规范 → 可编辑 A4 简历 HTML。

设计见 docs/2026-10-18-自动简历生成模块方案.md。与 /resume/project/rewrite 的区别：
那条链路只改写文字片段、不产出文件；本接口产出可直接打印/编辑的自包含 HTML。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query

from app.core.deps import get_conversation_store_service, get_resume_generation_service
from app.core.model_scope import request_model_scope
from app.core.ownership import (
    assert_conversation_owner,
    check_owner,
    get_owner_key,
    require_writable,
    request_owner_scope,
)
from app.core.response import fail, ok
from app.core.security import get_current_role
from app.repositories import DatabaseManager, ResumeGenerationRepository
from app.schemas.resume import ResumeGenerateRequest, ResumeRegenerateRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

_GENERATION_NOT_FOUND = "简历记录不存在"

# 示例简历的固定提示语：前端用它渲染黄条，避免各处自己写死文案对不上
_TEMPLATE_NOTE = "示例内容只用于展示版式和写法，不是你自己的经历，不能直接投递"


@router.get("/resume/template")
def get_resume_template() -> dict:
    """返回一份带示例内容的简历 HTML，供「先看看模板」预览。

    刻意不查会话、不校验 owner：示例里没有任何用户数据，产物也不落库、不落盘、
    不写会话消息（见 ResumeGenerationService.render_sample_template）。
    """
    try:
        html = get_resume_generation_service().render_sample_template()
    except ValueError as exc:
        # 模板文件缺失属于部署问题，对用户说人话，细节留在服务端日志
        logger.exception("示例简历渲染失败")
        return fail(f"模板暂不可用：{exc}", code=500)
    except Exception:
        logger.exception("示例简历渲染失败")
        return fail("模板暂不可用，请稍后重试", code=500)
    return ok({"html": html, "is_template": True, "note": _TEMPLATE_NOTE}, message="模板已加载")


def _assert_generation_owner(generation_id: str, owner_key: str, *, writable: bool = False) -> None:
    """第三层授权：generation_id 直取子表，回查会话归属。

    越权与不存在都抛同一句「简历记录不存在」——如果越权回「会话不存在」、
    不存在回「简历记录不存在」，两句话的差异本身就是一次存在性探测。
    """
    session = DatabaseManager.get_session()
    try:
        entity = ResumeGenerationRepository.get_by_generation_id(session, generation_id)
        if entity is None:
            raise ValueError(_GENERATION_NOT_FOUND)
        try:
            owner = check_owner(session, entity.conversation_id, owner_key)
        except ValueError:
            raise ValueError(_GENERATION_NOT_FOUND) from None
        if writable:
            require_writable(owner, owner_key)
    finally:
        session.close()


@router.post("/resume/generate")
def generate_resume(
    payload: ResumeGenerateRequest,
    role: str = Depends(get_current_role),
    owner_key: str = Depends(get_owner_key),
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

    # 生成会读会话文档、写一条 generation 记录，属于写操作
    get_conversation_store_service().ensure_conversation_writable(
        conversation_id, owner_key=owner_key
    )

    try:
        with request_model_scope(payload.model, role), request_owner_scope(owner_key):
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
    owner_key: str = Depends(get_owner_key),
) -> dict:
    """会话内的简历版本列表（不含正文，供侧栏树与历史切换）。"""
    conversation_id = (conversation_id or "").strip()
    if not conversation_id:
        return ok({"generations": []}, message="查询成功")
    assert_conversation_owner(conversation_id, owner_key)
    return ok(
        {"generations": get_resume_generation_service().list_generations(conversation_id, limit)},
        message="查询成功",
    )


@router.get("/resume/generations/{generation_id}")
def get_resume_generation(generation_id: str, owner_key: str = Depends(get_owner_key)) -> dict:
    """单版详情：结构化数据 + HTML 正文 + 告警。

    HTML 文件若被清理，服务端会用 data_json 原地重渲染（响应里
    html_regenerated=true），前端不需要感知。
    """
    try:
        _assert_generation_owner(generation_id, owner_key)
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
    owner_key: str = Depends(get_owner_key),
) -> dict:
    """补字段后重渲染，产出新一版（0 次 LLM，约 20ms）。"""
    extra = {
        str(k).strip(): str(v).strip()
        for k, v in (payload.extra_fields or {}).items()
        if str(k).strip() and str(v).strip()
    }
    try:
        # 重渲染会新增一条 generation 记录，属于写操作
        _assert_generation_owner(generation_id, owner_key, writable=True)
        result = get_resume_generation_service().rerender(generation_id, extra_fields=extra or None)
    except ValueError as exc:
        return fail(str(exc), code=400)
    except Exception:
        logger.exception("简历重渲染失败：generation_id=%s", generation_id)
        return fail("简历重渲染失败，请稍后重试", code=500)
    result.pop("html_path", None)
    return ok(result, message="已生成新一版")
