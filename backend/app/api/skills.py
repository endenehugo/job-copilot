"""内置技能清单接口：供输入框的技能选择面板渲染。

只读静态资源（resources/skills/*/SKILL.md 的 frontmatter），不鉴权——
访客也要能看见有哪些技能可选，和 /models 同理。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.response import ok
from app.utils import skill_loader

router = APIRouter(prefix="/api/v1", tags=["skills"])


@router.get("/skills")
def list_skill_catalog() -> dict:
    """返回可选择的技能（已隐藏模块自有与后端跑不了的技能，见 SKILL_META）。"""
    return ok({"skills": skill_loader.list_skills()})
