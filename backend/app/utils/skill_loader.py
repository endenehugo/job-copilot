"""内置求职技能（ASu-skills，MIT）的加载器。

来源：https://github.com/Hisn00w/ASu-skills
每个技能是一个目录：SKILL.md（YAML frontmatter 含 name/description + 正文指南）+ references/。

采用"渐进式加载"模式：技能目录清单（名称 + 一句话描述，每条几十 token）由
catalog_prompt() 生成进 Agent 系统提示；模型判断用户任务匹配某技能时，
通过 load_skill 按需加载完整指南再执行，避免把全部指南常驻上下文。
"""

from __future__ import annotations

import os
import re

from app.utils import ResourceUtils

_SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _skills_root() -> str:
    return ResourceUtils.get_resource_path("skills")


def _parse_frontmatter(path: str) -> dict:
    """解析 SKILL.md 头部的 YAML frontmatter（只取 name/description 两个标量字段）。"""
    front: dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    if not lines or lines[0].strip() != "---":
        return front
    for line in lines[1:]:
        if line.strip() == "---":
            break
        match = re.match(r"^([a-zA-Z_]+):\s*(.*)$", line)
        if match:
            front[match.group(1)] = match.group(2).strip()
    return front


def list_skills() -> list[dict]:
    """扫描 resources/skills/*/SKILL.md，返回技能目录清单。"""
    skills: list[dict] = []
    root = _skills_root()
    if not os.path.isdir(root):
        return skills
    for dirname in sorted(os.listdir(root)):
        path = os.path.join(root, dirname, "SKILL.md")
        if not os.path.isfile(path):
            continue
        front = _parse_frontmatter(path)
        skills.append({
            "name": front.get("name") or dirname,
            "description": front.get("description", ""),
            "dirname": dirname,
        })
    return skills


def load_skill(name: str) -> str:
    """按名称加载技能完整指南（SKILL.md 原文）。

    name 只允许小写字母/数字/连字符，防止路径穿越。
    技能不存在抛 FileNotFoundError。
    """
    name = (name or "").strip()
    if not _SKILL_NAME_PATTERN.fullmatch(name):
        raise ValueError(f"非法技能名: {name!r}")
    path = os.path.join(_skills_root(), name, "SKILL.md")
    if not os.path.isfile(path):
        raise FileNotFoundError(name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def catalog_prompt() -> str:
    """生成进系统提示的技能清单文本（名称 + 一句话描述）。"""
    lines = []
    for skill in list_skills():
        desc = skill["description"].split("；")[0].split("。")[0]
        lines.append(f"- {skill['name']}：{desc}")
    return "\n".join(lines)
