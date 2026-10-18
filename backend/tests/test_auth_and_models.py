"""管理员认证与角色模型白名单测试。

覆盖：
- auth 原语（密码哈希、JWT 签发/校验/过期/伪造）
- /auth/login 成功与失败路径
- /auth/me 的角色判定（无 token = user，带有效 admin token = admin）
- /models 的角色清单隔离
- resolve_model 白名单校验与默认值回落
- request_model_scope 的 ContextVar 传递（含流式生成器场景）
- /conversation/chat 传入越权模型 → 400（不触发 LLM）

运行：cd backend && D:/conda/python.exe -m pytest tests/test_auth_and_models.py -q
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

from app.core import security
from app.core.config import settings
from app.core.model_policy import (
    ROLE_ADMIN,
    ROLE_USER,
    allowed_models,
    default_model,
    resolve_model,
)
from app.core.model_scope import current_chat_model, request_model_scope
from app.core.security import create_token, decode_token, hash_password, verify_password
from app.repositories import AdminUserRepository, Base, DatabaseManager
import app.repositories.admin_user_repository  # 确保 AdminUser 注册到 Base.metadata

_TEST_DB_URL = "sqlite:///file:auth_test?mode=memory&cache=shared&uri=true"


@pytest.fixture(scope="module")
def sqlite_engine():
    # 先导入 app.main 让全部仓储模型注册进 Base.metadata，再建表——
    # 否则 _clear_db 遍历 sorted_tables 时会遇到未建的表（knowledge_entries 等）
    import app.main  # noqa: F401
    engine = create_engine(_TEST_DB_URL, future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def _patch_db(sqlite_engine):
    factory = scoped_session(
        sessionmaker(bind=sqlite_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    )

    @classmethod
    def _test_get_session(cls):
        return factory()

    @classmethod
    def _test_ensure_tables(cls):
        pass

    @classmethod
    def _test_remove_session(cls):
        factory.remove()

    original = (DatabaseManager.get_session, DatabaseManager.ensure_tables, DatabaseManager.remove_session)
    DatabaseManager.get_session = _test_get_session
    DatabaseManager.ensure_tables = _test_ensure_tables
    DatabaseManager.remove_session = _test_remove_session
    yield
    factory.remove()
    (DatabaseManager.get_session, DatabaseManager.ensure_tables, DatabaseManager.remove_session) = original


@pytest.fixture(autouse=True)
def _clear_db(sqlite_engine, _patch_db):
    with sqlite_engine.connect() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
        conn.commit()
    yield


@pytest.fixture()
def client(_patch_db):
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _fixed_secret(monkeypatch):
    """统一测试密钥，避免 fallback 随机密钥导致用例间 token 不互通。"""
    monkeypatch.setattr(security, "_fallback_secret", "test-secret")
    monkeypatch.setattr(settings, "auth_secret", "")
    yield


@pytest.fixture()
def admin_user(_patch_db):
    session = DatabaseManager.get_session()
    try:
        repo = AdminUserRepository()
        entity = repo.create(session, "admin", hash_password("secret123"))
        session.commit()
        return entity
    finally:
        DatabaseManager.remove_session()


# ---------------------------------------------------------------------------
# 1. auth 原语
# ---------------------------------------------------------------------------

def test_password_hash_roundtrip():
    hashed = hash_password("s3cret")
    assert hashed != "s3cret"
    assert verify_password("s3cret", hashed)
    assert not verify_password("wrong", hashed)


def test_verify_password_rejects_corrupted_hash():
    assert not verify_password("x", "not-a-bcrypt-hash")


def test_token_roundtrip_and_expiry():
    token = create_token("admin", expire_hours=1)
    payload = decode_token(token)
    assert payload["role"] == "admin"
    assert payload["sub"] == "admin"

    # 过期 token：签发时就已过期 → None
    expired = create_token("admin", expire_hours=-1)
    assert decode_token(expired) is None


def test_decode_token_rejects_garbage_and_forgery():
    assert decode_token("") is None
    assert decode_token("not.a.jwt") is None
    # 用错误密钥签的 token 必须被拒绝
    import jwt as pyjwt
    forged = pyjwt.encode({"sub": "x", "role": "admin", "exp": int(time.time()) + 9999}, "evil", algorithm="HS256")
    assert decode_token(forged) is None


# ---------------------------------------------------------------------------
# 2. 角色模型白名单
# ---------------------------------------------------------------------------

def test_allowed_models_isolated_by_role():
    user_models = allowed_models(ROLE_USER)
    admin_models = allowed_models(ROLE_ADMIN)
    assert user_models == ["qwen-flash", "qwen-plus"]
    assert set(user_models) < set(admin_models)
    assert len(admin_models) == len(settings.llm_selectable_models)
    # 返回副本，改坏不影响全局
    user_models.append("hack")
    assert allowed_models(ROLE_USER) == ["qwen-flash", "qwen-plus"]


def test_default_model_by_role():
    assert default_model(ROLE_USER) == "qwen-flash"
    assert default_model(ROLE_ADMIN) == settings.llm_model


def test_resolve_model_rules():
    # 空 → 角色默认
    assert resolve_model(ROLE_USER, "") == "qwen-flash"
    assert resolve_model(ROLE_USER, None) == "qwen-flash"
    # 白名单内
    assert resolve_model(ROLE_USER, "qwen-plus") == "qwen-plus"
    # 越权 → ValueError
    with pytest.raises(ValueError):
        resolve_model(ROLE_USER, "qwen3.8-max")
    with pytest.raises(ValueError):
        resolve_model(ROLE_USER, "kimi-k3")
    # 管理员可用全部清单
    for model in settings.llm_selectable_models:
        assert resolve_model(ROLE_ADMIN, model) == model


# ---------------------------------------------------------------------------
# 3. ContextVar 传递
# ---------------------------------------------------------------------------

def test_request_model_scope_sets_and_resets():
    assert current_chat_model() == settings.llm_model  # 作用域外 = 全局默认
    with request_model_scope("qwen-plus", ROLE_USER):
        assert current_chat_model() == "qwen-plus"
    assert current_chat_model() == settings.llm_model  # 退出后复位


def test_request_model_scope_visible_in_sync_generator():
    """模拟流式链路：scope 在生成器内部开启，线程池消费时可读。"""

    def consumer():
        return current_chat_model()

    with request_model_scope("qwen-flash", ROLE_USER):
        assert consumer() == "qwen-flash"

    def scoped_generator():
        with request_model_scope("qwen-flash", ROLE_USER):
            yield consumer()

    assert list(scoped_generator()) == ["qwen-flash"]


# ---------------------------------------------------------------------------
# 4. API：login / me / models
# ---------------------------------------------------------------------------

def _headers(token: str) -> dict:
    return {"X-Admin-Token": token}


def test_login_success_returns_admin_token(client, admin_user):
    resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "secret123"})
    body = resp.json()
    assert body["code"] == "success"
    assert body["data"]["role"] == "admin"
    assert body["data"]["username"] == "admin"
    assert decode_token(body["data"]["token"])


def test_login_rejects_wrong_password(client, admin_user):
    resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "bad"})
    assert resp.json()["code"] == 401


def test_login_rejects_unknown_user(client, admin_user):
    resp = client.post("/api/v1/auth/login", json={"username": "ghost", "password": "x"})
    # 与密码错误同话术，避免账号枚举
    assert resp.json()["code"] == 401


def test_login_requires_credentials(client):
    resp = client.post("/api/v1/auth/login", json={"username": "", "password": ""})
    assert resp.json()["code"] == 400


def test_me_defaults_to_user_without_token(client):
    body = client.get("/api/v1/auth/me").json()
    assert body["data"]["role"] == "user"


def test_me_recognizes_admin_token(client):
    token = create_token("admin")
    body = client.get("/api/v1/auth/me", headers=_headers(token)).json()
    assert body["data"]["role"] == "admin"


def test_me_treats_forged_token_as_user(client):
    import jwt as pyjwt
    forged = pyjwt.encode({"sub": "x", "role": "admin", "exp": int(time.time()) + 9999}, "evil", algorithm="HS256")
    body = client.get("/api/v1/auth/me", headers=_headers(forged)).json()
    assert body["data"]["role"] == "user"


def test_models_endpoint_by_role(client):
    user_body = client.get("/api/v1/models").json()
    assert user_body["data"]["role"] == "user"
    assert user_body["data"]["models"] == ["qwen-flash", "qwen-plus"]
    assert user_body["data"]["default"] == "qwen-flash"

    admin_body = client.get("/api/v1/models", headers=_headers(create_token("admin"))).json()
    assert admin_body["data"]["role"] == "admin"
    assert admin_body["data"]["models"] == settings.llm_selectable_models


# ---------------------------------------------------------------------------
# 5. 聊天端点的越权模型拦截（在调用 LLM 之前快速失败）
# ---------------------------------------------------------------------------

def test_chat_rejects_model_outside_role_whitelist(client):
    resp = client.post(
        "/api/v1/conversation/chat",
        json={"conversation_id": "c1", "query": "hi", "model": "qwen3.8-max"},
    )
    body = resp.json()
    assert body["code"] == 400
    assert "不在当前角色的可选清单内" in body["message"]


def test_chat_stream_rejects_model_outside_role_whitelist(client):
    resp = client.post(
        "/api/v1/conversation/chat/stream",
        json={"conversation_id": "c1", "query": "hi", "model": "kimi-k3"},
    )
    body = resp.json()
    assert body["code"] == 400
    assert "不在当前角色的可选清单内" in body["message"]


def test_chat_accepts_model_within_role_whitelist(client):
    """白名单内的模型不拦截（后续失败发生在 LLM mock 之外，属 e2e 范畴）。"""
    with patch("app.api.conversations.get_conversation_chat_service") as get_svc:
        get_svc.return_value.chat.return_value = {"answer": "ok", "warnings": []}
        resp = client.post(
            "/api/v1/conversation/chat",
            json={"conversation_id": "c1", "query": "hi", "model": "qwen-plus"},
        )
        assert resp.json()["code"] == "success"
        # 传给服务的请求确实发生在作用域内
        assert get_svc.return_value.chat.called


def test_ensure_initial_admin_creates_when_empty(monkeypatch, _patch_db):
    from app.api.auth import ensure_initial_admin
    monkeypatch.setattr(settings, "admin_username", "bootadmin")
    monkeypatch.setattr(settings, "admin_init_password", "bootpass")
    ensure_initial_admin()

    session = DatabaseManager.get_session()
    try:
        user = AdminUserRepository().get_by_username(session, "bootadmin")
        assert user is not None
        assert verify_password("bootpass", user.password_hash)
    finally:
        DatabaseManager.remove_session()


def test_ensure_initial_admin_skips_when_table_populated(monkeypatch, _patch_db, admin_user):
    from app.api.auth import ensure_initial_admin
    monkeypatch.setattr(settings, "admin_username", "another")
    monkeypatch.setattr(settings, "admin_init_password", "pass2")
    ensure_initial_admin()

    session = DatabaseManager.get_session()
    try:
        assert AdminUserRepository().get_by_username(session, "another") is None
    finally:
        DatabaseManager.remove_session()
