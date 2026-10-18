"""简历生成的持久化、主会话载荷与重渲染单测。

覆盖的都是"错了会静默坏掉"的点：
- 版本号重复（并发/重试时最容易漏）；
- 消息载荷写不进却让详情接口 500；
- HTML 文件被清理后卡片点开 404；
- 删除会话留下孤儿记录。

数据库用 SQLite 内存库替换 MySQL（与 tests/test_e2e_api.py 同一套打法）。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import scoped_session, sessionmaker

import app.repositories  # noqa: F401  确保所有模型注册到 Base.metadata
from app.repositories.mysql_base import Base, DatabaseManager
from app.repositories import (
    ConversationMessageMeta,
    ResumeGeneration,
    ResumeGenerationRepository,
)
from app.services import resume_generation_service as service
from app.services.conversation_store_service import ConversationStoreService
from app.utils import ResourceUtils

_REAL_RESOURCES = Path(__file__).resolve().parents[1] / "resources"


# ── 夹具 ────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def sqlite_engine():
    engine = create_engine(
        "sqlite:///file:resume_persist_test?mode=memory&cache=shared&uri=true",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _patch_db(sqlite_engine):
    """把 DatabaseManager 指到 SQLite 内存库，模块结束后复原。"""
    factory = scoped_session(
        sessionmaker(bind=sqlite_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    )
    original_get_session = DatabaseManager.__dict__.get("get_session")

    @classmethod
    def _test_get_session(cls):
        return factory()

    @classmethod
    def _test_ensure_tables(cls):
        pass

    @classmethod
    def _test_remove_session(cls):
        # 与线上一致：remove 会关闭当前线程的 session。
        # 做成空操作会让"某处忘了 close"的事务残留到下一次 with begin() 才暴露。
        factory.remove()

    DatabaseManager.get_session = _test_get_session
    DatabaseManager.ensure_tables = _test_ensure_tables
    DatabaseManager.remove_session = _test_remove_session
    yield
    factory.remove()
    DatabaseManager.get_session = original_get_session


@pytest.fixture
def store():
    svc = ConversationStoreService()
    cid = svc.create_conversation("持久化测试", "agent")["conversation_id"]
    yield svc, cid


@pytest.fixture
def template_ready():
    target = ResourceUtils.get_resource_path("resume_templates")
    shutil.copytree(_REAL_RESOURCES / "resume_templates", target, dirs_exist_ok=True)
    yield


_SAMPLE_DATA = {
    "profile": {
        "name": "张三",
        "headline": "本科 · 2022 年 6 月毕业",
        "phone": "138****8888",
        "email": "zhangsan@example.com",
        "target_role": "AI 应用开发工程师",
    },
    "education": [{"school": "深圳大学", "degree": "本科", "end": "2022.06"}],
    "works": [
        {
            "company": "某某科技",
            "role": "AI 应用开发工程师",
            "start": "2022.07",
            "end": "至今",
            "bullets": [
                {"text": "主导 RAG 落地，问答准确率 90%+", "basis": "resume", "source_span": "问答准确率 90%+"}
            ],
        }
    ],
    "projects": [],
    "skills": [{"label": "语言", "items": ["Go", "Python"]}],
    "missing": [],
}


def _save(conversation_id: str, *, version_data: dict | None = None) -> dict:
    svc = service.ResumeGenerationService()
    data = service.normalize_resume_data(version_data or _SAMPLE_DATA)
    html = "<html><body>简历</body></html>"
    filename, _ = svc.persist_html(html, conversation_id)
    return svc.save_generation(
        conversation_id=conversation_id,
        data=data,
        warnings=["数字 92% 在原文找不到出处"],
        materials={"document_id": "doc_1", "original_name": "简历.pdf", "resume_text": "x" * 100},
        filename=filename,
        html=html,
        stages={"total": 1.23},
    )


# ── 落库与版本 ───────────────────────────────────────────────────


def test_save_generation_persists_and_returns_version(store):
    _, cid = store
    first = _save(cid)
    second = _save(cid)

    assert first["generation_id"].startswith("rg_")
    assert first["version"] == 1
    assert second["version"] == 2

    session = DatabaseManager.get_session()
    try:
        rows = ResumeGenerationRepository.list_by_conversation_id(session, cid)
        assert [r.version for r in rows] == [2, 1]  # 倒序：最新一版在前
        assert rows[0].target_role == "AI 应用开发工程师"
        assert json.loads(rows[0].data_json)["profile"]["name"] == "张三"
        assert json.loads(rows[0].source_json)["document_id"] == "doc_1"
        assert rows[0].html_chars > 0
    finally:
        session.close()


def test_list_generations_returns_counts(store):
    store_svc, cid = store
    _save(cid)
    items = service.ResumeGenerationService().list_generations(cid)

    assert len(items) == 1
    assert items[0]["warnings_count"] == 1
    assert items[0]["missing_count"] == 0
    assert "data" not in items[0] and "html" not in items[0]


# ── 详情与自愈 ───────────────────────────────────────────────────


def test_load_generation_returns_html_and_data(store):
    _, cid = store
    saved = _save(cid)

    detail = service.ResumeGenerationService().load_generation(saved["generation_id"])
    assert detail["html"].startswith("<html>")
    assert detail["html_url"].endswith(".html")
    assert detail["data"]["profile"]["name"] == "张三"
    assert detail["html_regenerated"] is False
    assert detail["source"]["document_id"] == "doc_1"


def test_load_generation_rebuilds_when_html_missing(store, template_ready):
    """HTML 被清理后必须从 data_json 重建，而不是让用户点开卡片吃 404。"""
    _, cid = store
    saved = _save(cid)
    svc = service.ResumeGenerationService()

    before = svc.load_generation(saved["generation_id"])
    old_path = Path(ResourceUtils.get_resource_path("generated_docs")) / before["filename"]
    assert old_path.is_file()
    old_path.unlink()

    after = svc.load_generation(saved["generation_id"])
    assert after["html_regenerated"] is True
    assert after["html"].strip()  # 重新渲染出内容
    assert after["filename"] != before["filename"]  # 落了一份新文件
    assert (Path(ResourceUtils.get_resource_path("generated_docs")) / after["filename"]).is_file()

    # 回写后再次读取不再触发重建
    assert svc.load_generation(saved["generation_id"])["html_regenerated"] is False


def test_load_generation_unknown_id_raises(store):
    with pytest.raises(ValueError):
        service.ResumeGenerationService().load_generation("rg_not_exist")


# ── 主会话载荷 ───────────────────────────────────────────────────


def test_announce_writes_pair_with_meta(store):
    store_svc, cid = store
    saved = _save(cid)
    svc = service.ResumeGenerationService(conversation_store_service=store_svc)
    svc.announce_in_conversation({**saved, "conversation_id": cid,
                                  "data": service.normalize_resume_data(_SAMPLE_DATA),
                                  "warnings": ["w1"], "missing": []})

    detail = store_svc.get_conversation_detail(cid)
    assert detail["message_count"] == 2
    roles = [m["role"] for m in detail["messages"]]
    assert roles == ["user", "assistant"]

    assist = detail["messages"][1]
    assert assist["meta"]["kind"] == "resume"
    assert assist["meta"]["generation_id"] == saved["generation_id"]
    assert assist["meta"]["warnings_count"] == 1
    assert "A4 单栏" in assist["meta"]["meta_line"]


def test_message_meta_stored_in_separate_table(store):
    store_svc, cid = store
    saved = _save(cid)
    ids = store_svc.append_pair_with_meta(
        cid,
        "生成简历（目标岗位：AI 应用开发工程师）",
        "已生成",
        assistant_meta={"kind": "resume", "generation_id": saved["generation_id"]},
    )
    session = DatabaseManager.get_session()
    try:
        row = session.execute(
            select(ConversationMessageMeta).where(
                ConversationMessageMeta.message_id == ids["assistant_message_id"]
            )
        ).scalar_one()
        assert row.kind == "resume"
        assert json.loads(row.payload_json)["generation_id"] == saved["generation_id"]
    finally:
        session.close()


def test_dirty_meta_payload_does_not_break_detail(store):
    """载荷是 JSON 字符串，写坏了只能丢卡片，不能连会话详情一起 500。"""
    store_svc, cid = store
    ids = store_svc.append_pair_with_meta(cid, "提问", "回答")
    session = DatabaseManager.get_session()
    try:
        session.add(
            ConversationMessageMeta(
                message_id=ids["assistant_message_id"],
                conversation_id=cid,
                kind="resume",
                payload_json="{not json",
                created_at=__import__("datetime").datetime.now(),
            )
        )
        session.commit()
    finally:
        session.close()

    detail = store_svc.get_conversation_detail(cid)
    assist = [m for m in detail["messages"] if m["role"] == "assistant"][0]
    assert "meta" not in assist


# ── 级联删除 ─────────────────────────────────────────────────────


def test_delete_conversation_clears_generations_and_meta(store):
    store_svc, cid = store
    saved = _save(cid)
    store_svc.append_pair_with_meta(
        cid, "生成简历", "已生成", assistant_meta={"kind": "resume", "generation_id": saved["generation_id"]}
    )

    store_svc.delete_conversation(cid)

    session = DatabaseManager.get_session()
    try:
        assert session.execute(
            select(ResumeGeneration).where(ResumeGeneration.conversation_id == cid)
        ).first() is None
        assert session.execute(
            select(ConversationMessageMeta).where(ConversationMessageMeta.conversation_id == cid)
        ).first() is None
    finally:
        session.close()


# ── 端到端（mock 掉 LLM）─────────────────────────────────────────


def test_generate_persists_and_announces(store, template_ready, monkeypatch):
    store_svc, cid = store
    svc = service.ResumeGenerationService(conversation_store_service=store_svc)
    monkeypatch.setattr(
        svc,
        "collect_materials",
        lambda conversation_id: {
            "document_id": "doc_9",
            "original_name": "简历.txt",
            "resume_text": "主导 RAG 落地，问答准确率 90%+",
            "jd_brief": "",
        },
    )
    monkeypatch.setattr(
        svc,
        "extract",
        lambda materials, extra_fields=None: (service.normalize_resume_data(_SAMPLE_DATA), 0.01),
    )

    result = svc.generate(cid)

    assert result["generation_id"].startswith("rg_")
    assert result["version"] == 1
    assert result["html_url"].endswith(".html")
    assert "html_path" in result  # 路由层负责剥掉，服务层保留便于排查

    detail = store_svc.get_conversation_detail(cid)
    assist = [m for m in detail["messages"] if m["role"] == "assistant"][0]
    assert assist["meta"]["generation_id"] == result["generation_id"]
    assert "已按" in assist["content"]


def test_generate_survives_message_write_failure(store, template_ready, monkeypatch):
    """写消息失败不能把已经生成好的简历一起丢掉。"""

    class _BrokenStore:
        def append_pair_with_meta(self, *args, **kwargs):
            raise RuntimeError("db down")

    _, cid = store
    svc = service.ResumeGenerationService(conversation_store_service=_BrokenStore())
    monkeypatch.setattr(
        svc,
        "collect_materials",
        lambda conversation_id: {
            "document_id": "doc_9",
            "original_name": "简历.txt",
            "resume_text": "主导 RAG 落地",
            "jd_brief": "",
        },
    )
    monkeypatch.setattr(
        svc,
        "extract",
        lambda materials, extra_fields=None: (service.normalize_resume_data(_SAMPLE_DATA), 0.01),
    )

    result = svc.generate(cid)
    assert result["generation_id"].startswith("rg_")


# ── 重渲染 ───────────────────────────────────────────────────────


def test_rerender_applies_fields_and_creates_new_version(store, template_ready, monkeypatch):
    _, cid = store
    data = service.normalize_resume_data(
        {**_SAMPLE_DATA, "profile": {"name": "【待补：姓名】", "target_role": "AI 应用开发工程师"},
         "education": [{"school": "【待补：学校名称】", "degree": "本科"}], "missing": ["姓名", "学校名称"]}
    )
    svc = service.ResumeGenerationService()
    html = svc.render_html(data)
    filename, _ = svc.persist_html(html, cid)
    saved = svc.save_generation(
        conversation_id=cid, data=data, warnings=[], materials={}, filename=filename, html=html, stages={}
    )

    monkeypatch.setattr(
        svc, "collect_materials", lambda conversation_id: {"resume_text": "深圳大学 张三"}
    )
    monkeypatch.setattr(svc, "announce_in_conversation", lambda result: None)

    result = svc.rerender(saved["generation_id"], {"姓名": "张三", "学校名称": "深圳大学"})

    assert result["version"] == 2
    assert result["data"]["profile"]["name"] == "张三"
    assert result["data"]["education"][0]["school"] == "深圳大学"
    assert result["missing"] == []  # 补齐后缺口被摘掉
    assert set(result["applied_fields"]) == {"姓名", "学校名称"}
    assert result["stages"]["total"] >= 0


def test_rerender_ignores_unknown_fields(store, template_ready):
    _, cid = store
    svc = service.ResumeGenerationService()
    data = service.normalize_resume_data(_SAMPLE_DATA)
    html = svc.render_html(data)
    filename, _ = svc.persist_html(html, cid)
    saved = svc.save_generation(
        conversation_id=cid, data=data, warnings=[], materials={}, filename=filename, html=html, stages={}
    )
    svc.announce_in_conversation = lambda result: None

    result = svc.rerender(saved["generation_id"], {"身高": "180cm"})
    assert result["ignored_fields"] == ["身高"]
    assert result["data"]["profile"]["name"] == "张三"


# ── 文本与载荷构造 ───────────────────────────────────────────────


def test_build_message_meta_flags_pending_items():
    result = {
        "generation_id": "rg_1",
        "version": 1,
        "created_at": "2026-10-18T16:00:00",
        "data": service.normalize_resume_data(_SAMPLE_DATA),
        "warnings": ["w1", "w2"],
        "missing": ["学校名称"],
    }
    meta = service.build_message_meta(result)
    assert meta["kind"] == "resume"
    assert meta["warnings_count"] == 2
    assert meta["missing_count"] == 1
    assert "3 处待核对" in meta["meta_line"]
    assert "第 1 版" in meta["title"]


def test_build_lines_without_pending_items_reads_clean():
    result = {
        "generation_id": "rg_1",
        "version": 1,
        "data": service.normalize_resume_data(_SAMPLE_DATA),
        "warnings": [],
        "missing": [],
    }
    assert "目标岗位" in service.build_user_line(result)
    assert "未发现需要核对" in service.build_assistant_line(result)


def test_apply_extra_fields_clears_satisfied_missing():
    data = service.normalize_resume_data(
        {"profile": {"name": "【待补：姓名】"}, "education": [{"school": "【待补：学校名称】"}],
         "missing": ["姓名", "学校名称", "个人主页链接"]}
    )
    applied, ignored = service.apply_extra_fields(data, {"姓名": "李四", "学校": "深圳大学", "乱七八糟": "x"})
    assert set(applied) == {"姓名", "学校"}
    assert ignored == ["乱七八糟"]
    assert data["missing"] == ["个人主页链接"]
