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


def test_list_skills_hides_module_owned_and_unextendable(real_skills_root):
    """interview / make-resume 由专用模块承接，job-apply / contributor 后端跑不了，
    offer 没有对应存储与视图——这五类不进接口、也不进系统提示的技能目录。"""
    names = {s["name"] for s in list_skills()}
    assert names == {"job-match", "great-resume", "project-guide", "evidence-recap"}
    for s in list_skills():
        assert s["title"] and s["group"] and s["summary"], f"{s['name']} 缺展示字段"


def test_list_skills_all_includes_nine(real_skills_root):
    """全量模式（体检脚本用）仍能看到全部 9 个技能。"""
    names = {s["name"] for s in list_skills(visible_only=False)}
    assert {"job-match", "great-resume", "make-resume", "interview", "offer",
            "project-guide", "evidence-recap", "contributor", "job-apply"} <= names
    for s in list_skills(visible_only=False):
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
    assert "job-match" in result and "great-resume" in result
    assert "offer" not in result


def test_catalog_prompt_builds_lines(real_skills_root):
    prompt = catalog_prompt()
    assert "- job-match：" in prompt
    # 4 个可见技能 => 3 个换行
    assert prompt.count("\n") >= 3
    assert "make-resume" not in prompt and "interview" not in prompt and "offer" not in prompt


def test_agent_system_prompt_is_template_safe(real_skills_root, monkeypatch):
    """回归：技能摘要含 {简称} 等花括号文本时，系统提示必须转义，
    否则 ChatPromptTemplate 会把它们当成模板变量，所有 agent 请求报错。

    摘要现在是 SKILL_META 手写的，本身不含花括号，所以这里造一个含花括号的
    技能目录来守住这条防线（将来新增技能写了个模板占位符就会踩到）。
    """
    from langchain_core.prompts import ChatPromptTemplate

    from app.services.conversation_chat_service import build_agent_system_prompt
    from app.utils import skill_loader

    monkeypatch.setattr(
        skill_loader, "catalog_prompt", lambda: "- fake-skill：生成 导学-{简称}.md"
    )

    system = build_agent_system_prompt()
    prompt = ChatPromptTemplate.from_messages([
        ("system", system),
        ("human", "{query}"),
    ])
    rendered = prompt.invoke({"query": "测试"}).to_messages()[0].content
    assert "fake-skill" in rendered
    # 转义后花括号应原样保留在渲染结果里
    assert "{简称}" in rendered
