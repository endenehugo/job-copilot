"""示例简历模板（「先看看模板」）的单测。

这个功能的风险不在"渲染不出来"，而在两件会静默出事的地方：
- 示例里混进量化指标，用户删字不删数字就变成编造（项目的真实性 P0）；
- 示例走了落库/落盘路径，空壳占掉「第 1 版」并往会话里写假气泡。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.services import resume_generation_service as service
from app.utils import ResourceUtils

_REAL_RESOURCES = Path(__file__).resolve().parents[1] / "resources"


@pytest.fixture
def template_ready():
    """把真实模板拷进 conftest 指定的临时资源目录。"""
    target = ResourceUtils.get_resource_path("resume_templates")
    shutil.copytree(_REAL_RESOURCES / "resume_templates", target, dirs_exist_ok=True)
    yield


@pytest.fixture
def sample_data() -> dict:
    return service.normalize_resume_data(service.build_sample_resume_data())


# ── 示例数据本身 ─────────────────────────────────────────────────


def test_sample_data_covers_every_section(sample_data):
    """四节都要有内容：模板里每节都是 {% if data.x %}，空数组会让整节消失。"""
    assert sample_data["profile"]["name"]
    assert sample_data["education"], "教育背景为空会让该节不渲染"
    assert sample_data["works"], "工作经历为空会让该节不渲染"
    assert sample_data["projects"], "项目经历为空会让该节不渲染"
    assert sample_data["skills"], "专业技能为空会让该节不渲染"


def test_sample_has_no_pending_placeholders(sample_data):
    """示例是"填好的样子"，不该出现【待补】——那是骨架态的语义，混用会让用户分不清。"""
    blob = str(sample_data)
    assert "【待补" not in blob


def test_sample_bullets_carry_no_numbers(sample_data):
    """核心防线：描述性文案里一个数字都不能有。

    `audit_resume_data` 在素材为空时直接返回，模板态完全不受审计约束；示例里的
    "提升 40%""QPS 120" 一旦被保留进正式简历，就是编造指标。所以数字只能出现在
    时间、电话这类结构字段里，不能出现在 bullets 中。

    技能项不在此列：`Vue 3`「MySQL 8」里的数字是技术名的组成部分，不是业绩指标。
    """
    for section in ("works", "projects"):
        for item in sample_data[section]:
            for bullet in item["bullets"]:
                numbers = service._NUMBER_PATTERN.findall(bullet["text"])
                assert not numbers, f"{section} 的示例文案含数字 {numbers}：{bullet['text']}"


def test_sample_uses_fictional_org_names(sample_data):
    """用真实公司/学校名会让示例看起来像某人的真实简历。"""
    school = sample_data["education"][0]["school"]
    company = sample_data["works"][0]["company"]
    assert "示例" in school
    assert "某" in company


# ── 渲染 ────────────────────────────────────────────────────────


def test_sample_template_renders_all_section_titles(template_ready):
    html = service.ResumeGenerationService().render_sample_template()
    for title in ("教育背景", "工作经历", "项目经历", "专业技能"):
        assert title in html, f"渲染结果缺少「{title}」分区"


def test_render_sample_template_never_persists(template_ready, monkeypatch):
    """示例必须走"只读渲染"：不落盘、不落库、不写会话消息。

    这里把三个写路径全部换成"一调就炸"，用来锁住这条边界——
    否则以后谁顺手把 render_sample_template 改成 generate(...)，
    空壳就会固定占掉第 1 版，对话里还会多出一对假气泡。
    """
    svc = service.ResumeGenerationService()

    def _boom(*args, **kwargs):
        raise AssertionError("示例模板不允许写盘/写库/写消息")

    monkeypatch.setattr(svc, "persist_html", _boom)
    monkeypatch.setattr(svc, "save_generation", _boom)
    monkeypatch.setattr(svc, "announce_in_conversation", _boom)

    html = svc.render_sample_template()
    assert "<html" in html.lower()


def test_sample_route_registered():
    """接口路径与 GET 方法存在：前端「先看看模板」按钮直接打这个地址。"""
    from app.api.resume_generation import router

    routes = {(r.path, tuple(sorted(r.methods))) for r in router.routes}
    assert ("/api/v1/resume/template", ("GET",)) in routes
