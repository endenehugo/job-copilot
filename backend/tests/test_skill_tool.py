"""内置技能（ASu-skills）加载器与工具的回归测试。"""

from pathlib import Path

import pytest

from app.utils import ResourceUtils
from app.utils.skill_loader import catalog_prompt, list_skills, load_skill
from app.tools.skill import SkillTool


@pytest.fixture
def real_skills_root():
    """技能单测使用仓库内真实 resources/skills（conftest 默认指向临时目录）。"""
    old = ResourceUtils._RESOURCE_PATH
    ResourceUtils.init(str(Path(__file__).resolve().parents[1] / "resources"))
    yield
    ResourceUtils._RESOURCE_PATH = old


def test_catalog_contains_nine_asu_skills(real_skills_root):
    skills = list_skills()
    names = {s["name"] for s in skills}
    assert {"job-match", "great-resume", "make-resume", "interview", "offer",
            "project-guide", "evidence-recap", "contributor", "job-apply"} <= names
    for s in skills:
        assert s["description"], f"{s['name']} 缺少 description"


def test_load_skill_returns_full_guide(real_skills_root):
    content = load_skill("job-match")
    assert "# /job-match" in content
    assert "匹配状态" in content  # 证据矩阵关键词


def test_load_skill_rejects_traversal_and_unknown(real_skills_root):
    with pytest.raises(ValueError):
        load_skill("../secrets")
    with pytest.raises(FileNotFoundError):
        load_skill("no-such-skill")


def test_skill_tool_errors_as_string(real_skills_root):
    tool = SkillTool()
    result = tool.invoke({"skill_name": "no-such-skill"})
    assert isinstance(result, str) and result.startswith("错误") and "job-match" in result


def test_skill_tool_list_mode(real_skills_root):
    tool = SkillTool()
    result = tool.invoke({"skill_name": "list"})
    assert "job-match" in result and "offer" in result


def test_catalog_prompt_builds_lines(real_skills_root):
    prompt = catalog_prompt()
    assert "- job-match：" in prompt
    assert prompt.count("\n") >= 8


def test_agent_system_prompt_is_template_safe(real_skills_root):
    """回归：技能描述含 {简称} 等花括号文本时，系统提示必须转义，
    否则 ChatPromptTemplate 会把它们当成模板变量，所有 agent 请求报错。"""
    from langchain_core.prompts import ChatPromptTemplate

    from app.services.conversation_chat_service import build_agent_system_prompt

    system = build_agent_system_prompt()
    prompt = ChatPromptTemplate.from_messages([
        ("system", system),
        ("human", "{query}"),
    ])
    rendered = prompt.invoke({"query": "测试"}).to_messages()[0].content
    assert "job-match" in rendered
    # 转义后花括号应原样保留在渲染结果里
    assert "{简称}" in rendered
