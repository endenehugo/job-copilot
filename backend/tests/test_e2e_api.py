"""全功能 E2E 测试：覆盖 37 个 API 端点。

策略：
- SQLite 内存库替换 MySQL（monkeypatch DatabaseManager 类变量）
- Mock 所有 LLM 调用（ChatTongyi），避免外部依赖
- Mock FAISS 向量索引操作
- 按功能模块组织，每个端点测成功路径 + 关键错误路径

运行：
    cd backend && py -3.12 -m pytest tests/test_e2e_api.py -v --tb=short
"""

from __future__ import annotations

import io
import json
import os
import tempfile
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

from app.repositories.mysql_base import Base, DatabaseManager
import app.repositories.knowledge_entry_repository  # 确保 KnowledgeEntry 注册到 Base.metadata


# ---------------------------------------------------------------------------
# 1. 测试夹具：SQLite 内存库 + LLM mock
# ---------------------------------------------------------------------------

_TEST_DB_URL = "sqlite:///file:e2e_test?mode=memory&cache=shared&uri=true"


@pytest.fixture(scope="module")
def sqlite_engine():
    engine = create_engine(_TEST_DB_URL, future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def _patch_db(sqlite_engine):
    factory = scoped_session(
        sessionmaker(bind=sqlite_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    )

    original_get_session = DatabaseManager.get_session.__func__ if hasattr(DatabaseManager.get_session, '__func__') else DatabaseManager.__dict__.get('get_session')

    @classmethod
    def _test_get_session(cls):
        return factory()

    @classmethod
    def _test_ensure_tables(cls):
        pass

    @classmethod
    def _test_remove_session(cls):
        factory.remove()

    DatabaseManager.get_session = _test_get_session
    DatabaseManager.ensure_tables = _test_ensure_tables
    DatabaseManager.remove_session = _test_remove_session
    yield
    factory.remove()
    DatabaseManager.get_session = original_get_session
    DatabaseManager.ensure_tables = classmethod(lambda cls: None)
    DatabaseManager.remove_session = classmethod(lambda cls: None)


@pytest.fixture(autouse=True)
def _clear_db(sqlite_engine, _patch_db):
    with sqlite_engine.connect() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
        conn.commit()
    yield


@pytest.fixture()
def client(_patch_db):
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# 2. Mock LLM — 拦截所有 ChatTongyi 调用
# ---------------------------------------------------------------------------

_MOCK_ANSWER = "这是一段模拟的回答内容。"
_MOCK_JD_ANALYSIS = {
    "job_role": "后端开发工程师",
    "keywords": ["Python", "FastAPI", "MySQL"],
    "requirements": ["3年以上经验"],
    "bonus_points": ["开源贡献"],
    "strengths": ["技术栈匹配"],
    "gaps": ["缺少分布式经验"],
    "suggestions": ["建议补充项目描述"],
    "total_score": 75.0,
    "dimensions": {
        "skill_match": 80,
        "project_relevance": 70,
        "expression_quality": 75,
        "job_fitness": 75,
    },
}
_MOCK_INTERVIEW_QUESTIONS = [
    {"question": "请介绍一下你的项目经验。", "category": "project"},
    {"question": "你如何处理高并发场景？", "category": "technical"},
    {"question": "你的职业规划是什么？", "category": "behavioral"},
]
_MOCK_EVAL_RESULT = {
    "score": 8,
    "evaluation": "回答清晰，有具体例子。",
    "next_action": "continue",
    "follow_up": "能详细说说你解决过的最难的问题吗？",
}


def _mock_llm_invoke(*args, **kwargs):
    msg = MagicMock()
    msg.content = _MOCK_ANSWER
    return msg


def _mock_llm_stream(*args, **kwargs):
    for text in [_MOCK_ANSWER]:
        chunk = MagicMock()
        chunk.content = text
        chunk.tool_calls = []
        yield chunk


@pytest.fixture(autouse=True)
def _mock_all_llm(_patch_db):
    """Mock 所有 LLM 和外部服务调用。"""
    mock_tongyi = MagicMock()
    mock_tongyi.invoke = _mock_llm_invoke
    mock_tongyi.stream = _mock_llm_stream

    patches = []

    def _do_patch(target, **kwargs):
        p = patch(target, **kwargs)
        p.start()
        patches.append(p)

    _do_patch("langchain_community.chat_models.ChatTongyi", return_value=mock_tongyi)
    # app.core.llm_factory 在模块导入时就把 ChatTongyi 绑进了自己的命名空间，
    # 只打上面那一处是否生效取决于导入顺序；导入顺序靠后时 FallbackChatModel
    # 会继承到 Mock，构造时抛 StopIteration。这里补打工厂自己的符号，顺序无关。
    _do_patch("app.core.llm_factory.FallbackChatModel", return_value=mock_tongyi)
    # 这一代新模型走的是兼容模式客户端（ChatOpenAI），同样要拦，否则单测会真发网络请求
    _do_patch("app.core.llm_factory.CompatibleFallbackChatModel", return_value=mock_tongyi)
    _do_patch("app.services.context_compression_service.ContextCompressionService._get_llm", return_value=mock_tongyi)
    _do_patch("app.services.context_verification_service.ContextVerificationService.generate_verification_report",
              return_value={"verification": {"risk_level": "low", "issues": []}, "cited_answer": _MOCK_ANSWER})

    _do_patch("app.services.job_description_service.JobDescriptionService.analyze", return_value=_MOCK_JD_ANALYSIS)
    _do_patch("app.services.resume_scoring_service.ResumeScoringService.score",
              return_value={"total_score": 75.0, "dimensions": _MOCK_JD_ANALYSIS["dimensions"],
                            "strengths": _MOCK_JD_ANALYSIS["strengths"],
                            "gaps": _MOCK_JD_ANALYSIS["gaps"],
                            "suggestions": _MOCK_JD_ANALYSIS["suggestions"]})

    _do_patch("app.services.interview_simulation_service.InterviewSimulationService.start_interview",
              return_value={"questions": _MOCK_INTERVIEW_QUESTIONS})
    _do_patch("app.services.interview_simulation_service.InterviewSimulationService.evaluate_answer",
              return_value=_MOCK_EVAL_RESULT)

    _do_patch("app.services.project_rewrite_service.ProjectRewriteService.rewrite",
              return_value={"original": "测试项目", "optimized": "优化后的项目描述",
                            "techniques_used": ["STAR法则"], "improvements": ["更量化"]})

    _do_patch("app.services.image_analysis_service.ImageAnalysisService.extract_jd_from_screenshot",
              return_value="招聘后端开发工程师，要求Python、FastAPI、MySQL经验，3年以上。负责系统设计与开发。")

    _do_patch("app.services.export_service.ExportService.export_analysis_markdown",
              return_value="# 分析报告\n\n岗位：后端开发工程师")
    _do_patch("app.services.export_service.ExportService.export_interview_markdown",
              return_value="# 面试记录\n\n问题1：介绍一下项目经验")
    _do_patch("app.services.export_service.ExportService.export_project_rewrite_markdown",
              return_value="# 项目优化\n\n优化后描述")

    _do_patch("app.services.builtin_knowledge_service.BuiltinKnowledgeService.rebuild_index",
              return_value={"chunk_count": 10, "status": "ok"})
    _do_patch("app.services.builtin_knowledge_service.BuiltinKnowledgeService.add_entry",
              return_value={"approved": True, "entry_id": "ke_test123", "review": {"reason": ""}})
    _do_patch("app.services.builtin_knowledge_service.BuiltinKnowledgeService.list_entries",
              return_value=[])
    _do_patch("app.services.builtin_knowledge_service.BuiltinKnowledgeService.remove_entry",
              return_value=None)
    _do_patch("app.services.builtin_knowledge_service.BuiltinKnowledgeService.self_expand",
              return_value={"added": [], "rejected": []})
    _do_patch("app.services.builtin_knowledge_service.BuiltinKnowledgeService.retrieve",
              return_value=[])
    _do_patch("app.services.builtin_knowledge_service.BuiltinKnowledgeService.list_categories",
              return_value=["tech", "behavioral"])
    _do_patch("app.services.builtin_knowledge_service.BuiltinKnowledgeService.corpus_stats",
              return_value={"total": 5, "categories": {"tech": 3, "behavioral": 2}})

    _do_patch("app.services.document_index_service.DocumentIndexService.rebuild_conversation_index",
              return_value=None)
    _do_patch("app.services.document_index_service.DocumentIndexService.add_document_to_index",
              return_value={"chunk_count": 1, "mode": "incremental"})
    _do_patch("app.services.document_index_service.DocumentIndexService.delete_conversation_index",
              return_value=None)
    _do_patch("app.services.document_index_service.DocumentIndexService.get_context_with_details",
              return_value={"context": "", "documents": []})

    _do_patch("app.utils.api_key_checker.check_all",
              return_value=MagicMock(all_passed=True, results=[]))

    yield

    for p in reversed(patches):
        p.stop()


# ---------------------------------------------------------------------------
# 3. 辅助函数
# ---------------------------------------------------------------------------

def _create_conversation(client, title="测试会话", mode="agent"):
    resp = client.post("/api/v1/conversation/create", json={"title": title, "mode": mode})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == "success"
    return body["data"]


def _make_test_text_file(content="简历内容：Python开发工程师，3年经验"):
    return io.BytesIO(content.encode("utf-8"))


# ===========================================================================
# 4. 测试用例
# ===========================================================================

class TestRoot:
    def test_index(self, client):
        resp = client.get("/")
        body = resp.json()
        assert body["code"] == "success"
        assert body["data"]["name"] == "Job Copilot API"


class TestSystem:
    def test_health(self, client):
        resp = client.get("/api/v1/system/health")
        body = resp.json()
        assert body["code"] == "success"
        assert body["data"]["status"] == "ok"
        assert "time" in body["data"]

    def test_keycheck(self, client):
        resp = client.get("/api/v1/system/keycheck")
        body = resp.json()
        assert body["code"] == "success"
        assert "all_passed" in body["data"]


class TestConversation:
    def test_create(self, client):
        data = _create_conversation(client, title="我的求职")
        assert data["conversation_id"].startswith("conv_")
        assert data["title"] == "我的求职"

    def test_create_with_defaults(self, client):
        data = _create_conversation(client)
        assert data["title"] == "测试会话"

    def test_list(self, client):
        _create_conversation(client, title="会话A")
        _create_conversation(client, title="会话B")
        resp = client.get("/api/v1/conversation/list")
        body = resp.json()
        assert body["code"] == "success"
        assert len(body["data"]["conversations"]) == 2

    def test_detail(self, client):
        conv = _create_conversation(client)
        cid = conv["conversation_id"]
        resp = client.get(f"/api/v1/conversation/detail?conversation_id={cid}")
        body = resp.json()
        assert body["code"] == "success"
        assert body["data"]["conversation_id"] == cid
        assert body["data"]["messages"] == []

    def test_detail_empty_id(self, client):
        resp = client.get("/api/v1/conversation/detail?conversation_id=")
        body = resp.json()
        assert body["code"] == 400

    def test_detail_not_found(self, client):
        resp = client.get("/api/v1/conversation/detail?conversation_id=nonexistent")
        body = resp.json()
        assert body["code"] == 400

    def test_chat(self, client):
        conv = _create_conversation(client)
        cid = conv["conversation_id"]
        resp = client.post("/api/v1/conversation/chat", json={
            "conversation_id": cid, "query": "你好", "mode": "memory"
        })
        body = resp.json()
        assert body["code"] == "success"
        assert "answer" in body["data"]

    def test_chat_empty_cid(self, client):
        resp = client.post("/api/v1/conversation/chat", json={
            "conversation_id": "", "query": "你好"
        })
        body = resp.json()
        assert body["code"] == 400

    def test_chat_empty_query(self, client):
        conv = _create_conversation(client)
        resp = client.post("/api/v1/conversation/chat", json={
            "conversation_id": conv["conversation_id"], "query": ""
        })
        body = resp.json()
        assert body["code"] == 400

    def test_chat_stream(self, client):
        conv = _create_conversation(client)
        cid = conv["conversation_id"]
        resp = client.post("/api/v1/conversation/chat/stream", json={
            "conversation_id": cid, "query": "介绍一下RAG", "mode": "rag"
        })
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        text = resp.text
        assert "event: meta" in text
        assert "event: done" in text

    def test_delete(self, client):
        conv = _create_conversation(client)
        cid = conv["conversation_id"]
        resp = client.post("/api/v1/conversation/delete", json={"conversation_id": cid})
        body = resp.json()
        assert body["code"] == "success"
        detail = client.get(f"/api/v1/conversation/detail?conversation_id={cid}").json()
        assert detail["code"] == 400

    def test_delete_empty_id(self, client):
        resp = client.post("/api/v1/conversation/delete", json={"conversation_id": ""})
        body = resp.json()
        assert body["code"] == 400


class TestImage:
    def test_upload_image(self, client):
        conv = _create_conversation(client)
        cid = conv["conversation_id"]
        png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        resp = client.post("/api/v1/conversation/image/upload",
                           data={"conversation_id": cid},
                           files={"file": ("test.png", io.BytesIO(png_data), "image/png")})
        body = resp.json()
        assert body["code"] == "success"
        assert "image_url" in body["data"]

    def test_upload_invalid_ext(self, client):
        conv = _create_conversation(client)
        cid = conv["conversation_id"]
        resp = client.post("/api/v1/conversation/image/upload",
                           data={"conversation_id": cid},
                           files={"file": ("test.pdf", io.BytesIO(b"fake"), "application/pdf")})
        body = resp.json()
        assert body["code"] == 400

    def test_serve_image_not_found(self, client):
        conv = _create_conversation(client)
        resp = client.get(f"/conversation/image/{conv['conversation_id']}/nonexist.png")
        body = resp.json()
        assert body["code"] == 404

    def test_serve_generated_file_not_found(self, client):
        resp = client.get("/api/v1/conversation/file/nonexist.html")
        body = resp.json()
        assert body["code"] == 404

    def test_serve_generated_file_unsafe_name(self, client):
        resp = client.get("/api/v1/conversation/file/..%2F..%2Fetc/passwd")
        assert resp.status_code in (200, 404)


class TestDocument:
    def test_upload_and_delete(self, client):
        conv = _create_conversation(client)
        cid = conv["conversation_id"]
        resp = client.post("/api/v1/document/upload",
                           data={"conversation_id": cid},
                           files={"file": ("resume.txt", _make_test_text_file(), "text/plain")})
        body = resp.json()
        assert body["code"] == "success"
        doc = body["data"]["document"]
        assert doc["document_id"]

        del_resp = client.post("/api/v1/document/delete", json={"document_id": doc["document_id"]})
        del_body = del_resp.json()
        assert del_body["code"] == "success"

    def test_upload_empty_cid(self, client):
        resp = client.post("/api/v1/document/upload",
                           data={"conversation_id": ""},
                           files={"file": ("resume.txt", _make_test_text_file(), "text/plain")})
        body = resp.json()
        assert body["code"] == 400

    def test_delete_empty_id(self, client):
        resp = client.post("/api/v1/document/delete", json={"document_id": ""})
        body = resp.json()
        assert body["code"] == 400


class TestJob:
    def test_analyze(self, client):
        conv = _create_conversation(client)
        cid = conv["conversation_id"]
        client.post("/api/v1/document/upload",
                     data={"conversation_id": cid},
                     files={"file": ("resume.txt", _make_test_text_file(), "text/plain")})
        resp = client.post("/api/v1/job/analyze", json={
            "conversation_id": cid,
            "jd_text": "招聘Python后端开发，要求3年以上FastAPI经验，熟悉MySQL、Redis"
        })
        body = resp.json()
        assert body["code"] == "success"
        assert body["data"]["jd_analysis"]["job_role"] == "后端开发工程师"

    def test_analyze_no_resume(self, client):
        conv = _create_conversation(client)
        resp = client.post("/api/v1/job/analyze", json={
            "conversation_id": conv["conversation_id"],
            "jd_text": "招聘后端开发"
        })
        body = resp.json()
        assert body["code"] == 400

    def test_analyze_empty_params(self, client):
        resp = client.post("/api/v1/job/analyze", json={"conversation_id": "", "jd_text": ""})
        assert resp.json()["code"] == 400

    def test_latest_analysis(self, client):
        conv = _create_conversation(client)
        cid = conv["conversation_id"]
        client.post("/api/v1/document/upload",
                     data={"conversation_id": cid},
                     files={"file": ("r.txt", _make_test_text_file(), "text/plain")})
        client.post("/api/v1/job/analyze", json={"conversation_id": cid, "jd_text": "后端开发"})
        resp = client.get(f"/api/v1/job/analysis/latest?conversation_id={cid}")
        body = resp.json()
        assert body["code"] == "success"

    def test_latest_analysis_none(self, client):
        resp = client.get("/api/v1/job/analysis/latest?conversation_id=nonexist")
        body = resp.json()
        assert body["code"] == 404

    def test_list_analyses(self, client):
        conv = _create_conversation(client)
        cid = conv["conversation_id"]
        client.post("/api/v1/document/upload",
                     data={"conversation_id": cid},
                     files={"file": ("r.txt", _make_test_text_file(), "text/plain")})
        client.post("/api/v1/job/analyze", json={"conversation_id": cid, "jd_text": "JD1"})
        resp = client.get(f"/api/v1/job/analysis/list?conversation_id={cid}")
        body = resp.json()
        assert body["code"] == "success"
        assert len(body["data"]["analyses"]) >= 1

    def test_analyze_from_screenshot_no_image(self, client):
        conv = _create_conversation(client)
        resp = client.post("/api/v1/job/analyze-from-screenshot", json={
            "conversation_id": conv["conversation_id"],
            "image_url": "/conversation/image/xxx/nonexist.png"
        })
        body = resp.json()
        assert body["code"] == 400


class TestInterview:
    def _setup_with_resume(self, client):
        conv = _create_conversation(client)
        cid = conv["conversation_id"]
        client.post("/api/v1/document/upload",
                     data={"conversation_id": cid},
                     files={"file": ("r.txt", _make_test_text_file(), "text/plain")})
        client.post("/api/v1/job/analyze", json={"conversation_id": cid, "jd_text": "后端开发"})
        return cid

    def test_start(self, client):
        cid = self._setup_with_resume(client)
        resp = client.post("/api/v1/interview/start", json={
            "conversation_id": cid, "direction": "backend"
        })
        body = resp.json()
        assert body["code"] == "success"
        assert "session_id" in body["data"]
        assert len(body["data"]["questions"]) > 0

    def test_start_no_resume(self, client):
        conv = _create_conversation(client)
        resp = client.post("/api/v1/interview/start", json={
            "conversation_id": conv["conversation_id"]
        })
        body = resp.json()
        assert body["code"] == 400

    def test_answer_continue(self, client):
        cid = self._setup_with_resume(client)
        start_body = client.post("/api/v1/interview/start", json={"conversation_id": cid}).json()
        session_id = start_body["data"]["session_id"]
        resp = client.post("/api/v1/interview/answer", json={
            "session_id": session_id, "answer": "我做过一个高并发的订单系统", "question_index": 0
        })
        body = resp.json()
        assert body["code"] == "success"
        assert body["data"]["action"] == "continue"
        assert "evaluation" in body["data"]

    def test_answer_no_session(self, client):
        resp = client.post("/api/v1/interview/answer", json={
            "session_id": "nonexist", "answer": "测试"
        })
        body = resp.json()
        assert body["code"] == 404

    def test_answer_empty(self, client):
        resp = client.post("/api/v1/interview/answer", json={"session_id": "", "answer": ""})
        assert resp.json()["code"] == 400

    def test_list(self, client):
        cid = self._setup_with_resume(client)
        client.post("/api/v1/interview/start", json={"conversation_id": cid})
        resp = client.get(f"/api/v1/interview/list?conversation_id={cid}")
        body = resp.json()
        assert body["code"] == "success"
        assert len(body["data"]["sessions"]) >= 1

    def test_detail(self, client):
        cid = self._setup_with_resume(client)
        start_body = client.post("/api/v1/interview/start", json={"conversation_id": cid}).json()
        sid = start_body["data"]["session_id"]
        resp = client.get(f"/api/v1/interview/detail?session_id={sid}")
        body = resp.json()
        assert body["code"] == "success"
        assert body["data"]["session"]["session_id"] == sid
        assert len(body["data"]["messages"]) >= 1

    def test_detail_not_found(self, client):
        resp = client.get("/api/v1/interview/detail?session_id=nonexist")
        assert resp.json()["code"] == 404


class TestResume:
    def test_project_rewrite(self, client):
        conv = _create_conversation(client)
        cid = conv["conversation_id"]
        client.post("/api/v1/document/upload",
                     data={"conversation_id": cid},
                     files={"file": ("r.txt", _make_test_text_file(), "text/plain")})
        resp = client.post("/api/v1/resume/project/rewrite", json={
            "conversation_id": cid,
            "project_description": "我负责了一个电商系统的后端开发"
        })
        body = resp.json()
        assert body["code"] == "success"
        assert "optimized" in body["data"]

    def test_rewrite_empty_params(self, client):
        resp = client.post("/api/v1/resume/project/rewrite", json={
            "conversation_id": "", "project_description": ""
        })
        assert resp.json()["code"] == 400

    def test_versions_list(self, client):
        conv = _create_conversation(client)
        cid = conv["conversation_id"]
        client.post("/api/v1/document/upload",
                     data={"conversation_id": cid},
                     files={"file": ("r.txt", _make_test_text_file(), "text/plain")})
        resp = client.get(f"/api/v1/resume/versions/list?conversation_id={cid}")
        body = resp.json()
        assert body["code"] == "success"
        assert len(body["data"]["versions"]) >= 1

    def test_version_detail(self, client):
        conv = _create_conversation(client)
        cid = conv["conversation_id"]
        client.post("/api/v1/document/upload",
                     data={"conversation_id": cid},
                     files={"file": ("r.txt", _make_test_text_file(), "text/plain")})
        versions = client.get(f"/api/v1/resume/versions/list?conversation_id={cid}").json()["data"]["versions"]
        vid = versions[0]["version_id"]
        resp = client.get(f"/api/v1/resume/versions/detail?version_id={vid}")
        body = resp.json()
        assert body["code"] == "success"
        assert body["data"]["version_id"] == vid

    def test_version_detail_not_found(self, client):
        resp = client.get("/api/v1/resume/versions/detail?version_id=nonexist")
        assert resp.json()["code"] == 404

    def test_versions_compare(self, client):
        conv = _create_conversation(client)
        cid = conv["conversation_id"]
        client.post("/api/v1/document/upload",
                     data={"conversation_id": cid},
                     files={"file": ("r.txt", _make_test_text_file(), "text/plain")})
        resp = client.get(f"/api/v1/resume/versions/compare?conversation_id={cid}")
        body = resp.json()
        assert body["code"] == "success"
        assert "score_history" in body["data"]
        assert body["data"]["trend"] in ("up", "down", "stable")


class TestExport:
    def test_export_analysis(self, client):
        conv = _create_conversation(client)
        cid = conv["conversation_id"]
        client.post("/api/v1/document/upload",
                     data={"conversation_id": cid},
                     files={"file": ("r.txt", _make_test_text_file(), "text/plain")})
        client.post("/api/v1/job/analyze", json={"conversation_id": cid, "jd_text": "后端开发"})
        analyses = client.get(f"/api/v1/job/analysis/list?conversation_id={cid}").json()["data"]["analyses"]
        aid = analyses[0]["id"]
        resp = client.get(f"/api/v1/export/analysis?analysis_id={aid}")
        body = resp.json()
        assert body["code"] == "success"
        assert "markdown" in body["data"]

    def test_export_analysis_empty_id(self, client):
        resp = client.get("/api/v1/export/analysis?analysis_id=0")
        assert resp.json()["code"] == 400

    def test_export_interview(self, client):
        conv = _create_conversation(client)
        cid = conv["conversation_id"]
        client.post("/api/v1/document/upload",
                     data={"conversation_id": cid},
                     files={"file": ("r.txt", _make_test_text_file(), "text/plain")})
        client.post("/api/v1/job/analyze", json={"conversation_id": cid, "jd_text": "后端开发"})
        start = client.post("/api/v1/interview/start", json={"conversation_id": cid}).json()
        sid = start["data"]["session_id"]
        resp = client.get(f"/api/v1/export/interview?session_id={sid}")
        body = resp.json()
        assert body["code"] == "success"
        assert "markdown" in body["data"]

    def test_export_interview_empty(self, client):
        resp = client.get("/api/v1/export/interview?session_id=")
        assert resp.json()["code"] == 400

    def test_export_project_rewrite(self, client):
        resp = client.post("/api/v1/export/project-rewrite", json={
            "result": {"original": "项目A", "optimized": "项目A优化版"}
        })
        body = resp.json()
        assert body["code"] == "success"
        assert "markdown" in body["data"]

    def test_export_project_rewrite_empty(self, client):
        resp = client.post("/api/v1/export/project-rewrite", json={"result": {}})
        assert resp.json()["code"] == 400


class TestKnowledge:
    def test_rebuild(self, client):
        resp = client.post("/api/v1/knowledge/rebuild")
        body = resp.json()
        assert body["code"] == "success"

    def test_submit_entry(self, client):
        resp = client.post("/api/v1/knowledge/entries", json={"content": "面试常见技术问题"})
        body = resp.json()
        assert body["code"] == "success"
        assert body["data"]["approved"] is True

    def test_submit_empty(self, client):
        resp = client.post("/api/v1/knowledge/entries", json={"content": ""})
        body = resp.json()
        assert body["code"] == "success"

    def test_list_entries(self, client):
        resp = client.get("/api/v1/knowledge/entries")
        body = resp.json()
        assert body["code"] == "success"
        assert "entries" in body["data"]

    def test_delete_entry(self, client):
        resp = client.delete("/api/v1/knowledge/entries/fake_id")
        body = resp.json()
        assert body["code"] == "success" or body["code"] == 404

    def test_self_expand(self, client):
        resp = client.post("/api/v1/knowledge/self-expand", json={"count": 2})
        body = resp.json()
        assert body["code"] == "success"
        assert "added" in body["data"]

    def test_query(self, client):
        resp = client.get("/api/v1/knowledge/query?query=Python")
        body = resp.json()
        assert body["code"] == "success"

    def test_query_empty(self, client):
        resp = client.get("/api/v1/knowledge/query?query=")
        assert resp.json()["code"] == 400

    def test_categories(self, client):
        resp = client.get("/api/v1/knowledge/categories")
        body = resp.json()
        assert body["code"] == "success"
        assert "categories" in body["data"]

    def test_status(self, client):
        resp = client.get("/api/v1/knowledge/status")
        body = resp.json()
        assert body["code"] == "success"
        assert "total_documents" in body["data"]


class TestIncrementalSummary:
    """验证增量摘要持久化功能。"""

    def test_summary_persistence(self, client):
        conv = _create_conversation(client)
        cid = conv["conversation_id"]

        store = _patch_db_store()
        summary, until_id = store.get_conversation_summary(cid)
        assert summary == ""
        assert until_id == 0

    def test_build_history_includes_system(self, client):
        from app.services.conversation_chat_service import ConversationChatService
        from langchain_core.messages import SystemMessage

        history = [
            {"role": "system", "content": "[上下文摘要] 测试摘要"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"},
        ]
        msgs = ConversationChatService._build_history(history)
        assert len(msgs) == 3
        assert isinstance(msgs[0], SystemMessage)

    def test_compress_history_incremental(self, client):
        from app.services.context_compression_service import ContextCompressionService

        svc = ContextCompressionService()
        history = [{"id": i, "role": "user", "content": f"消息{i}"} for i in range(1, 26)]
        old_summary = "之前的对话涉及Python项目"

        result, new_summary, new_watermark = svc.compress_history(
            history, old_summary=old_summary, max_messages=20
        )

        assert new_watermark == 5
        assert isinstance(new_summary, str)
        assert len(new_summary) > 0
        assert result[0]["role"] == "system"
        assert len(result) == 21

    def test_messages_after_query(self, client):
        conv = _create_conversation(client)
        cid = conv["conversation_id"]

        store = _patch_db_store()
        store.append_message_pair(cid, "问题1", "回答1", "agent")
        store.append_message_pair(cid, "问题2", "回答2", "agent")

        all_msgs = store.get_conversation_messages(cid)
        assert len(all_msgs) == 4
        assert all_msgs[0]["id"] > 0

        first_id = all_msgs[0]["id"]
        after_msgs = store.get_messages_after(cid, first_id)
        assert len(after_msgs) == 3

    def test_save_and_read_summary(self, client):
        conv = _create_conversation(client)
        cid = conv["conversation_id"]

        store = _patch_db_store()
        store.save_conversation_summary(cid, "这是测试摘要", 10)

        summary, until_id = store.get_conversation_summary(cid)
        assert summary == "这是测试摘要"
        assert until_id == 10

    def test_optimistic_lock(self, client):
        conv = _create_conversation(client)
        cid = conv["conversation_id"]

        store = _patch_db_store()
        store.save_conversation_summary(cid, "摘要v1", 10)
        store.save_conversation_summary(cid, "摘要v2", 5)

        summary, until_id = store.get_conversation_summary(cid)
        assert summary == "摘要v1"
        assert until_id == 10


def _patch_db_store():
    from app.services.conversation_store_service import ConversationStoreService
    return ConversationStoreService()
