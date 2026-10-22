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

# 技能的展示元信息。exposed=False 表示**对外不暴露**：
# 既不进 /api/v1/skills，也不进系统提示的技能目录。
# 三类原因：①本产品已有专用模块 + 确定性流水线承接（interview、make-resume），
# 让用户再选一遍等于绕开模块的状态机和模板渲染；
# ②后端没有对应执行能力（job-apply 要浏览器、contributor 要 GitHub 写权限）；
# ③产出无处可放（offer 是一份本地进度表，本产品没有对应的存储与视图）。
# title/group 由后端给，前端不要硬编码。
SKILL_META: dict[str, dict] = {
    "job-match": {
        "title": "岗位匹配分析",
        "group": "求职判断",
        "summary": "对照 JD 逐项找证据，判断值不值得投",
    },
    "great-resume": {
        "title": "简历经历提升",
        "group": "求职判断",
        "summary": "把真实经历改写成经得起追问的表达",
    },
    "project-guide": {
        "title": "项目导学与面经",
        "group": "面试准备",
        "summary": "把项目整理成源码课程与面经",
    },
    "evidence-recap": {
        "title": "交付证据复盘",
        "group": "面试准备",
        "summary": "把交付过程还原成可核验的证据链",
    },
    "offer": {
        "title": "秋招进度管理",
        "group": "过程管理",
        "summary": "记录投递、面试与 Offer 进度",
        "exposed": False,
    },
    "interview": {
        "title": "模拟面试",
        "group": "面试准备",
        "summary": "见左侧「模拟面试」模块",
        "exposed": False,
    },
    "make-resume": {
        "title": "制作简历文件",
        "group": "交付文件",
        "summary": "见左侧「简历生成」模块",
        "exposed": False,
    },
    "job-apply": {"title": "求职申请填写", "group": "过程管理", "exposed": False},
    "contributor": {"title": "开源贡献", "group": "过程管理", "exposed": False},
}


def skill_meta(name: str) -> dict:
    """取技能展示元信息，未登记的名字兜底成自身。"""
    meta = SKILL_META.get(name) or {}
    return {
        "title": meta.get("title") or name,
        "group": meta.get("group") or "其他",
        "summary": meta.get("summary") or "",
        "exposed": meta.get("exposed", True),
    }


def _auto_summary(description: str) -> str:
    """未登记 summary 的技能：取 description 首句并截断。

    面板一行放不下整句（最长的一条 78 字），所以登记的技能都手写 summary，
    这里只是兜底，保证新加的 SKILL.md 不会显示一整段。
    """
    text = (description or "").split("；")[0].split("。")[0].strip()
    head, sep, tail = text.partition("：")
    # "中文岗位匹配分析技能：xxx" 这类前缀信息量为零（title 已经说了）
    if sep and len(head) <= 16 and tail:
        text = tail
    return text if len(text) <= 46 else text[:45].rstrip("，、；") + "…"



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


def list_skills(visible_only: bool = True) -> list[dict]:
    """扫描 resources/skills/*/SKILL.md，返回技能目录清单。

    visible_only=True（默认）跳过 exposed=False 的技能，让接口、系统提示、
    工具提示三处自动保持一致——否则会出现"用户看不到、模型还在系统提示里看到"。
    需要全量（例如体检脚本）时传 False。
    """
    skills: list[dict] = []
    root = _skills_root()
    if not os.path.isdir(root):
        return skills
    for dirname in sorted(os.listdir(root)):
        path = os.path.join(root, dirname, "SKILL.md")
        if not os.path.isfile(path):
            continue
        front = _parse_frontmatter(path)
        name = front.get("name") or dirname
        description = front.get("description", "")
        meta = skill_meta(name)
        if visible_only and not meta["exposed"]:
            continue
        skills.append({
            "name": name,
            "title": meta["title"],
            "group": meta["group"],
            "summary": meta["summary"] or _auto_summary(description),
            "description": description,
            "dirname": dirname,
        })
    # 按 SKILL_META 的登记顺序输出（产品排序），未登记的技能排在最后。
    # 前端按原顺序平铺渲染，键盘上下键不需要再做一次排序。
    order = {name: index for index, name in enumerate(SKILL_META)}
    skills.sort(key=lambda item: (order.get(item["name"], 999), item["name"]))
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


def list_references(name: str) -> list[str]:
    """列出某个技能下的 reference 文件相对路径（如 references/page-balance-qa.md）。

    技能不存在或没有 references/ 时返回空列表；名称非法抛 ValueError。
    """
    name = (name or "").strip()
    if not _SKILL_NAME_PATTERN.fullmatch(name):
        raise ValueError(f"非法技能名: {name!r}")
    refs_dir = os.path.join(_skills_root(), name, "references")
    if not os.path.isdir(refs_dir):
        return []
    found: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(refs_dir):
        for filename in sorted(filenames):
            if not filename.endswith(".md"):
                continue
            full = os.path.join(dirpath, filename)
            found.append(os.path.relpath(full, os.path.join(_skills_root(), name)).replace("\\", "/"))
    return sorted(found)


def load_reference(name: str, reference: str) -> str:
    """加载技能下某个 reference 的正文。

    reference 为相对于技能目录的路径，只允许引用 references/ 下的 .md 文件，
    防止路径穿越。文件不存在抛 FileNotFoundError。
    """
    name = (name or "").strip()
    if not _SKILL_NAME_PATTERN.fullmatch(name):
        raise ValueError(f"非法技能名: {name!r}")
    skill_dir = os.path.realpath(os.path.join(_skills_root(), name))
    target = os.path.realpath(os.path.join(skill_dir, (reference or "").strip()))
    if not target.startswith(skill_dir + os.sep):
        raise ValueError(f"越界的 reference 路径: {reference!r}")
    if not target.startswith(os.path.join(skill_dir, "references") + os.sep):
        raise ValueError("reference 必须位于技能目录的 references/ 下")
    if not target.endswith(".md") or not os.path.isfile(target):
        raise FileNotFoundError(reference)
    with open(target, "r", encoding="utf-8") as f:
        return f.read()


def catalog_prompt() -> str:
    """生成进系统提示的技能清单文本（名称 + 一句话描述）。"""
    lines = []
    for skill in list_skills():
        lines.append(f"- {skill['name']}：{skill['summary']}")
    return "\n".join(lines)
