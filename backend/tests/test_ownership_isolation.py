"""会话隔离的行为测试。

与其它单测不同，这里**走真实 HTTP 链路**（TestClient）：身份签发在中间件里、
校验在依赖里、过滤在 SQL 里，只测 service 层无法证明这三层接上了。

三个身份各建一个 TestClient（cookie jar 相互独立）：
- 访客 A / 访客 B：中间件各自签发不同令牌
- 管理员：带 X-Admin-Token

覆盖点：
① 两个访客互相看不见对方会话
② 越权的错误信息与"不存在"完全一致（不泄漏存在性）
③ legacy（owner_key=''）访客不可见、管理员可见
④ demo 会话访客可见但只读
⑤ 篡改 cookie 视为新访客，不报错也不给权限
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

from app.core.ownership import ADMIN_OWNER, ADMIN_OWNER_KEY, DEMO_OWNER, LEGACY_OWNER
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.mysql_base import Base, DatabaseManager

_TEST_DB_URL = "sqlite:///file:ownership_test?mode=memory&cache=shared&uri=true"

NOT_FOUND = "会话不存在"


@pytest.fixture(scope="module")
def engine():
    engine = create_engine(_TEST_DB_URL, future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _patch_db(engine):
    factory = scoped_session(
        sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    )
    original_get = DatabaseManager.__dict__["get_session"]
    original_ensure = DatabaseManager.__dict__["ensure_tables"]
    original_remove = DatabaseManager.__dict__["remove_session"]

    DatabaseManager.get_session = classmethod(lambda cls: factory())
    DatabaseManager.ensure_tables = classmethod(lambda cls: None)
    DatabaseManager.remove_session = classmethod(lambda cls: factory.remove())
    try:
        yield
    finally:
        DatabaseManager.get_session = original_get
        DatabaseManager.ensure_tables = original_ensure
        DatabaseManager.remove_session = original_remove
        factory.remove()


@pytest.fixture(autouse=True)
def _clear_db(engine):
    from sqlalchemy import inspect

    existing = set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            if table.name in existing:
                connection.execute(table.delete())
        connection.commit()
    yield


def _client():
    """每次调用都是一个身份独立的客户端（各自持有自己的 cookie）。"""
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def visitor_a():
    return _client()


@pytest.fixture()
def visitor_b():
    return _client()


@pytest.fixture()
def admin():
    from app.core.security import create_token

    client = _client()
    client.headers.update({"X-Admin-Token": create_token("tester")})
    return client


def _create(client, title="会话") -> str:
    resp = client.post("/api/v1/conversation/create", json={"title": title, "mode": "agent"})
    body = resp.json()
    assert body["code"] == "success", body
    return body["data"]["conversation_id"]


def _set_owner(conversation_id: str, owner_key: str) -> None:
    """直接改库：模拟 demo 样本与 legacy 历史数据，这两类只能由运维/管理员造出来。"""
    session = DatabaseManager.get_session()
    try:
        with session.begin():
            entity = ConversationRepository().get_by_conversation_id(session, conversation_id)
            entity.owner_key = owner_key
    finally:
        session.close()


def _detail(client, conversation_id: str) -> dict:
    return client.get(f"/api/v1/conversation/detail?conversation_id={conversation_id}").json()


def _list_ids(client) -> list[str]:
    body = client.get("/api/v1/conversation/list").json()
    return [item["conversation_id"] for item in body["data"]["conversations"]]


# ── ① 身份签发 ────────────────────────────────────────────────────


def test_middleware_issues_visitor_cookie(visitor_a):
    body = visitor_a.get("/api/v1/conversation/list").json()
    assert body["code"] == "success"
    # 首屏就要能拿到身份，前端不需要额外初始化请求
    assert "jc_visitor" in visitor_a.cookies
    # HttpOnly 由浏览器端生效，这里能读到的是客户端 jar，不代表可被 JS 取用


def test_two_visitors_get_different_identities(visitor_a, visitor_b):
    cid_a = _create(visitor_a, "A 的会话")
    cid_b = _create(visitor_b, "B 的会话")
    assert cid_a not in _list_ids(visitor_b)
    assert cid_b not in _list_ids(visitor_a)


# ── ② 越权与不存在同文案 ─────────────────────────────────────────


def test_detail_of_other_visitor_looks_like_missing(visitor_a, visitor_b):
    cid = _create(visitor_a)
    other = _detail(visitor_b, cid)
    missing = _detail(visitor_b, "conv_20200101000000_abcdef")
    assert other["code"] == 400
    assert other["message"] == NOT_FOUND
    assert other["message"] == missing["message"], "越权与不存在必须同文案，否则成了存在性探针"


def test_delete_of_other_visitor_does_nothing(visitor_a, visitor_b):
    cid = _create(visitor_a)
    resp = visitor_b.post("/api/v1/conversation/delete", json={"conversation_id": cid}).json()
    assert resp["message"] == NOT_FOUND
    # 关键：数据必须还在。授权失败如果发生在删除之后，就是最糟的那种失败
    assert _detail(visitor_a, cid)["code"] == "success"


def test_chat_on_other_visitor_session_is_rejected(visitor_a, visitor_b):
    cid = _create(visitor_a)
    resp = visitor_b.post(
        "/api/v1/conversation/chat",
        json={"conversation_id": cid, "query": "你好", "mode": "agent"},
    ).json()
    assert resp["message"] == NOT_FOUND


def test_pin_on_other_visitor_session_is_rejected(visitor_a, visitor_b):
    cid = _create(visitor_a)
    resp = visitor_b.post(
        "/api/v1/conversation/pin", json={"conversation_id": cid, "pinned": True}
    ).json()
    assert resp["message"] == NOT_FOUND


def test_document_upload_to_other_session_is_rejected(visitor_a, visitor_b):
    cid = _create(visitor_a)
    resp = visitor_b.post(
        "/api/v1/document/upload",
        data={"conversation_id": cid},
        files={"file": ("a.txt", b"hello", "text/plain")},
    ).json()
    assert resp["message"] == NOT_FOUND


def test_child_resource_by_id_is_checked(visitor_a, visitor_b):
    """第三层：按 analysis_id 直取的端点必须回查父表。"""
    from datetime import datetime

    from app.repositories import JobAnalysisRepository

    cid = _create(visitor_a)
    session = DatabaseManager.get_session()
    try:
        with session.begin():
            entity = JobAnalysisRepository.create(
                session,
                conversation_id=cid,
                jd_text="某岗位 JD",
                created_at=datetime.now(),
            )
            analysis_id = entity.id
    finally:
        session.close()

    body = visitor_b.get(f"/api/v1/job/analysis/detail?analysis_id={analysis_id}").json()
    missing = visitor_b.get("/api/v1/job/analysis/detail?analysis_id=999999").json()
    assert body["code"] == 404
    # 这类端点对"不存在"回的是「分析记录不存在」，越权必须回同一句，
    # 否则两句文案的差异能用来判断这个 analysis_id 是否真实存在
    assert body["message"] == missing["message"]
    # 同一个 id，属主自己能看
    assert visitor_a.get(f"/api/v1/job/analysis/detail?analysis_id={analysis_id}").json()["code"] == "success"


# ── ③ legacy ─────────────────────────────────────────────────────


def test_legacy_session_is_admin_only(visitor_a, admin):
    cid = _create(visitor_a)
    _set_owner(cid, LEGACY_OWNER)

    assert cid not in _list_ids(visitor_a)
    assert _detail(visitor_a, cid)["message"] == NOT_FOUND

    assert cid in _list_ids(admin)
    assert _detail(admin, cid)["code"] == "success"


# ── ④ demo 样本 ──────────────────────────────────────────────────


def test_demo_session_is_visible_but_readonly(visitor_a):
    cid = _create(visitor_a)
    _set_owner(cid, DEMO_OWNER)

    assert cid in _list_ids(visitor_a)
    detail = _detail(visitor_a, cid)
    assert detail["code"] == "success"
    assert detail["data"]["readonly"] is True

    chat = visitor_a.post(
        "/api/v1/conversation/chat",
        json={"conversation_id": cid, "query": "你好", "mode": "agent"},
    ).json()
    assert chat["code"] == 403, "演示样本必须挡在写入之前，否则第一个访客就把样本聊脏了"

    assert visitor_a.post(
        "/api/v1/conversation/pin", json={"conversation_id": cid, "pinned": True}
    ).json()["code"] == 403
    assert visitor_a.post(
        "/api/v1/conversation/delete", json={"conversation_id": cid}
    ).json()["code"] == 403


def test_admin_can_write_demo_session(admin):
    """样本归管理员维护，他不受只读限制。"""
    cid = _create(admin)
    _set_owner(cid, DEMO_OWNER)
    assert _detail(admin, cid)["data"]["readonly"] is False


# ── ⑤ 管理员视角 ─────────────────────────────────────────────────


def test_admin_sees_visitor_sessions_with_owner_and_time(visitor_a, admin):
    cid = _create(visitor_a, "访客试用")
    rows = {
        item["conversation_id"]: item
        for item in admin.get("/api/v1/conversation/list").json()["data"]["conversations"]
    }
    assert cid in rows
    row = rows[cid]
    # 管理员要能分辨"这条是谁在用、什么时候"
    assert row["owner_key"].startswith("v_")
    assert row["created_at"] and row["updated_at"]


def test_admin_created_session_is_tagged_admin(admin, visitor_a):
    cid = _create(admin)
    rows = {
        item["conversation_id"]: item
        for item in admin.get("/api/v1/conversation/list").json()["data"]["conversations"]
    }
    assert rows[cid]["owner_key"] == ADMIN_OWNER_KEY
    # 反过来不成立：访客看不到管理员自己聊的东西
    assert cid not in _list_ids(visitor_a)


# ── ⑥ 篡改 cookie ────────────────────────────────────────────────


def _cookie_value(client) -> str:
    """取访客令牌。不用 client.cookies.get(name)：httpx 对同名不同 domain 的
    cookie 会抛 CookieConflict，而测试用域名与空域名会各存一份。"""
    for cookie in client.cookies.jar:
        if cookie.name == "jc_visitor":
            return cookie.value
    raise AssertionError("没有签发访客 cookie")


def test_tampered_cookie_becomes_new_visitor(visitor_a):
    cid = _create(visitor_a)
    original = _cookie_value(visitor_a)

    visitor_a.cookies.set("jc_visitor", original[:-3] + "abc")
    resp = _detail(visitor_a, cid)
    # 不报错、也不放行：直接当成一个新访客，重新签发
    assert resp["message"] == NOT_FOUND
    assert _cookie_value(visitor_a) != original


# ── ⑦ repository 层的过滤 ────────────────────────────────────────


def test_list_recent_with_empty_owner_keys_returns_nothing():
    """空列表要返回空结果而不是全表：调用方拿不到身份时应当"看不到"。"""
    session = DatabaseManager.get_session()
    try:
        with session.begin():
            ConversationRepository().create(
                session,
                conversation_id="conv_legacy_case",
                title="历史",
                mode="agent",
                message_count=0,
                last_message_preview="",
                created_at=__import__("datetime").datetime.now(),
                updated_at=__import__("datetime").datetime.now(),
            )
        assert ConversationRepository().list_recent(session, owner_keys=[]) == []
        admin_view = ConversationRepository().list_recent(session, owner_keys=None)
        assert [c.conversation_id for c in admin_view] == ["conv_legacy_case"]
    finally:
        session.close()


def test_admin_owner_sentinel_is_not_persisted(admin):
    """`*` 只该出现在查询侧，落库的是 admin。"""
    cid = _create(admin)
    session = DatabaseManager.get_session()
    try:
        entity = ConversationRepository().get_by_conversation_id(session, cid)
        assert entity.owner_key == ADMIN_OWNER_KEY != ADMIN_OWNER
    finally:
        session.close()
