"""简历生成：skill 规范切片、结构化规范化、模板渲染与事实审计的单测。

覆盖的都是"错了会静默出错"的点：
- 切片把 node 脚本指令漏进 prompt（会让模型去执行不存在的东西）；
- 模型输出脏数据导致模板渲染 500；
- 审计抓不到编造的数字（本项目的 P0 风险）。
"""

from __future__ import annotations

import copy
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


# ── 规范切片 ──────────────────────────────────────────────────────


def test_extract_sections_keeps_only_whitelisted():
    markdown = (
        "# 技能名\n\n"
        "## 输入清单\n- 姓名、电话\n\n"
        "## 模板与资源\n默认模板壳是 assets/asu-resume/template.html\n\n"
        "## 默认排版\n- 默认 A4\n"
    )
    spec = service.extract_sections(markdown, ("输入清单", "默认排版"))
    assert "姓名、电话" in spec
    assert "默认 A4" in spec
    assert "asu-resume/template.html" not in spec


def test_extract_sections_returns_empty_when_titles_missing():
    markdown = "## 别的章节\n内容\n"
    assert service.extract_sections(markdown, ("输入清单",)) == ""


def test_build_skill_spec_drops_script_and_template_instructions(monkeypatch):
    """SKILL.md 里讲 node 脚本/模板资产的段落不能进 prompt。"""
    guide = (
        "# make-resume\n\n"
        "## 输入清单\n- 姓名\n\n"
        "## 工作流程\n5. 执行 node scripts/build-asu-resume.mjs 内联样式\n\n"
        "## 默认排版\n- 默认 A4，白底\n"
    )
    reference = (
        "# 默认模板\n\n"
        "## 视觉参数\n- 分区标题 13pt\n\n"
        "## 资源约束（本平台）\nassets/templates-html/ 不存在\n"
    )
    monkeypatch.setattr(service.skill_loader, "load_skill", lambda name: guide)
    monkeypatch.setattr(service.skill_loader, "load_reference", lambda name, ref: reference)

    spec = service.build_skill_spec()
    assert "默认 A4" in spec
    assert "分区标题 13pt" in spec
    assert "build-asu-resume.mjs" not in spec
    assert "templates-html" not in spec


def test_build_skill_spec_tolerates_missing_skill(monkeypatch):
    def _raise(*_args, **_kwargs):
        raise FileNotFoundError("make-resume")

    monkeypatch.setattr(service.skill_loader, "load_skill", _raise)
    assert service.build_skill_spec() == ""


# ── 数据规范化 ────────────────────────────────────────────────────


def test_normalize_fills_placeholders_for_missing_name():
    data = service.normalize_resume_data({"profile": {"name": None}})
    assert data["profile"]["name"].startswith("【待补：")
    assert "姓名" in data["missing"]


def test_normalize_tolerates_string_bullets_and_non_list_fields():
    data = service.normalize_resume_data({
        "projects": [{
            "name": "项目 A",
            "stack": "Milvus",          # 该是数组却给了字符串
            "bullets": ["直接给字符串的一条描述"],
        }],
        "skills": "不是数组",
    })
    assert data["projects"][0]["stack"] == []
    assert data["projects"][0]["bullets"][0]["basis"] == "resume"
    assert data["skills"] == []


def test_normalize_drops_entries_without_identity():
    data = service.normalize_resume_data({"works": [{"company": "", "role": ""}, {"company": "某公司"}]})
    assert len(data["works"]) == 1
    assert data["works"][0]["company"] == "某公司"


def test_normalize_renames_skill_items_to_tags():
    """Jinja 里 group.items 会解析成 dict 方法，必须换键名。"""
    data = service.normalize_resume_data({"skills": [{"label": "语言", "items": ["Go", "Python"]}]})
    assert data["skills"][0]["tags"] == ["Go", "Python"]
    assert "items" not in data["skills"][0]


# ── 渲染 ─────────────────────────────────────────────────────────


def _sample_data() -> dict:
    return service.normalize_resume_data({
        "profile": {
            "name": "张三",
            "headline": "本科 · 2022 年 6 月毕业",
            "phone": "138****8888",
            "email": "zhangsan@example.com",
            "city": "深圳",
            "target_role": "AI 应用开发工程师",
        },
        "education": [{"school": "某某大学", "degree": "本科", "major": "软件工程",
                       "start": "2018.09", "end": "2022.06"}],
        "works": [{"company": "某某科技有限公司", "role": "AI 应用开发工程师",
                   "start": "2022.07", "end": "至今",
                   "bullets": [{"text": "主导 RAG 架构落地", "basis": "resume",
                                "source_span": "设计并落地 RAG 架构"}]}],
        "projects": [{"name": "企业级智能知识库问答系统", "role": "AI 应用开发工程师",
                      "start": "2024.01", "end": "2026.01",
                      "stack": ["Milvus", "文心一言"],
                      "bullets": [{"text": "问答准确率 90%+，响应 ≤2s", "basis": "resume",
                                   "source_span": "问答准确率 90%+"}]}],
        "skills": [{"label": "语言", "items": ["Go", "Python"]}],
        "missing": [],
    })


def test_render_produces_self_contained_html(template_ready):
    html = service.ResumeGenerationService().render_html(_sample_data())

    assert html.startswith("<!doctype html>")
    assert "{{" not in html and "{%" not in html, "存在未解析的模板变量"
    assert "张三" in html and "企业级智能知识库问答系统" in html
    # 自包含：不引用任何外部资源（照片 URL 除外，本用例无照片）
    assert "http://" not in html and "https://" not in html
    assert "@page" in html and "size: A4" in html


def test_render_marks_pending_placeholders(template_ready):
    data = service.normalize_resume_data({
        "profile": {"name": None},
        "works": [{"company": "某公司", "bullets": [{"text": "【待补：团队规模】"}]}],
    })
    html = service.ResumeGenerationService().render_html(data)
    assert 'class="pending"' in html
    assert "【待补：团队规模】" in html


def test_render_escapes_user_text(template_ready):
    data = service.normalize_resume_data({
        "profile": {"name": "张三"},
        "works": [{"company": "某公司", "bullets": [{"text": "<script>alert(1)</script>"}]}],
    })
    html = service.ResumeGenerationService().render_html(data)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_build_view_model_does_not_mutate_input():
    """name_html / html_text 是渲染期字段，写回 data 会被接口原样返回给前端。"""
    data = _sample_data()
    before = copy.deepcopy(data)
    service.build_view_model(data)
    assert data == before


# ── 事实审计 ──────────────────────────────────────────────────────


_SOURCE = "主导 RAG 全流程开发。问答准确率 90%+，支持百万级文档秒级检索，响应 ≤2s。Milvus 检索。"


def test_audit_passes_when_numbers_come_from_source():
    data = service.normalize_resume_data({
        "projects": [{
            "name": "知识库",
            "stack": ["Milvus"],
            "bullets": [{"text": "问答准确率 90%+，响应 ≤2s", "basis": "resume",
                         "source_span": "问答准确率 90%+，支持百万级文档秒级检索，响应 ≤2s"}],
        }],
    })
    assert service.audit_resume_data(data, _SOURCE) == []


def test_audit_flags_fabricated_number():
    data = service.normalize_resume_data({
        "projects": [{
            "name": "知识库",
            "stack": ["Milvus"],
            "bullets": [{"text": "准确率从 78% 提升至 92%", "basis": "resume",
                         "source_span": "问答准确率 90%+"}],
        }],
    })
    warnings = service.audit_resume_data(data, _SOURCE)
    text = " ".join(warnings)
    assert "78" in text and "92" in text


def test_audit_flags_fabricated_tech_and_missing_span():
    data = service.normalize_resume_data({
        "projects": [{
            "name": "知识库",
            "stack": ["Docling"],
            "bullets": [{"text": "集成多格式解析", "basis": "resume", "source_span": ""}],
        }],
    })
    warnings = service.audit_resume_data(data, _SOURCE)
    assert any("Docling" in w for w in warnings)
    assert any("未提供原文出处" in w for w in warnings)


def test_audit_flags_span_not_in_source():
    data = service.normalize_resume_data({
        "projects": [{
            "name": "知识库",
            "bullets": [{"text": "搭建了离线评测体系", "basis": "resume",
                         "source_span": "搭建了离线评测体系并把准确率做到 92%"}],
        }],
    })
    warnings = service.audit_resume_data(data, _SOURCE)
    assert any("出处片段在原文中查无对应" in w for w in warnings)


def test_audit_skips_year_like_numbers():
    data = service.normalize_resume_data({
        "projects": [{"name": "知识库",
                      "bullets": [{"text": "2024 年上线", "basis": "resume",
                                   "source_span": "主导 RAG 全流程开发"}]}],
    })
    assert service.audit_resume_data(data, _SOURCE) == []


def test_audit_caps_warning_count():
    bullets = [{"text": f"指标 {i}00 提升", "basis": "resume", "source_span": ""} for i in range(30)]
    data = service.normalize_resume_data({"projects": [{"name": "P", "bullets": bullets}]})
    warnings = service.audit_resume_data(data, _SOURCE)
    assert any("告警已截断" in w for w in warnings)
    assert len(warnings) <= service._MAX_WARNINGS + 1


def test_head_meta_dedupes_city_already_in_headline():
    data = service.normalize_resume_data({
        "profile": {"name": "张三", "headline": "本科 · 2022 年 6 月毕业 · 现居深圳",
                    "city": "深圳", "phone": "138****8888"},
    })
    view = service.build_view_model(data)
    assert view["head_meta"] == ["本科 · 2022 年 6 月毕业 · 现居深圳", "138****8888"]


def test_normalize_filters_jd_gaps_from_missing():
    """实测模型会把 JD 匹配缺口塞进 missing，污染"简历缺字段"的语义。"""
    data = service.normalize_resume_data({
        "profile": {"name": "张三"},
        "missing": ["是否掌握 C++（JD 硬性要求）", "是否具备 Linux 后台经验", "个人主页链接"],
    })
    assert "个人主页链接" in data["missing"]
    assert all("C++" not in m and "Linux" not in m for m in data["missing"])


def test_normalize_rejects_identity_line_as_school():
    data = service.normalize_resume_data({
        "profile": {"name": "张三"},
        "education": [{"school": "本科 · 2022 年 6 月毕业", "degree": "本科"}],
    })
    assert data["education"][0]["school"] == "【待补：学校名称】"
