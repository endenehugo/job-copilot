"""会话置顶的回归测试（SQLite 内存库，不依赖 MySQL）。

覆盖四条容易翻车的点：
① 置顶后置顶项排最前，取消置顶回到按 updated_at 的原位
② 取列表的 limit 截断不能把置顶的旧会话截掉（所以排序必须在 SQL 里做）
③ 置顶不写 updated_at；重复置顶保留首次时间（幂等，且已置顶项之间次序稳定）
④ 会话不存在时给 400 语义，不是 500
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

from app.repositories.conversation_repository import ConversationRepository
from app.repositories.mysql_base import Base, DatabaseManager

_TEST_DB_URL = "sqlite:///file:pin_test?mode=memory&cache=shared&uri=true"


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
    # 别的模块可能在此之后才把表注册进 Base.metadata（如 knowledge_entries），
    # 那些表在这个 SQLite 库里并不存在，只清真正建出来的
    from sqlalchemy import inspect

    existing = set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            if table.name in existing:
                connection.execute(table.delete())
        connection.commit()
    yield


@pytest.fixture()
def store():
    from app.services.conversation_store_service import ConversationStoreService

    return ConversationStoreService()


def _seed(cid: str, title: str, minutes_ago: int) -> None:
    """直接落库并指定 updated_at，保证排序断言与机器快慢无关。"""
    stamp = datetime(2026, 10, 22, 12, 0, 0) - timedelta(minutes=minutes_ago)
    session = DatabaseManager.get_session()
    try:
        with session.begin():
            ConversationRepository().create(
                session,
                conversation_id=cid,
                title=title,
                mode="agent",
                message_count=0,
                last_message_preview="",
                created_at=stamp,
                updated_at=stamp,
            )
    finally:
        session.close()


def _ids(items) -> list[str]:
    return [item["conversation_id"] for item in items]


def test_pin_moves_to_top_and_unpin_restores_order(store):
    _seed("c_old", "最旧", 30)
    _seed("c_mid", "中间", 20)
    _seed("c_new", "最新", 10)

    assert _ids(store.list_conversations(limit=10)) == ["c_new", "c_mid", "c_old"]

    store.set_conversation_pinned("c_old", True)
    assert _ids(store.list_conversations(limit=10)) == ["c_old", "c_new", "c_mid"]

    store.set_conversation_pinned("c_old", False)
    assert _ids(store.list_conversations(limit=10)) == ["c_new", "c_mid", "c_old"]


def test_pin_survives_list_limit(store):
    """limit=2 时置顶的旧会话仍要在，否则前端永远看不到自己的置顶。"""
    _seed("c_1", "最旧", 50)
    for index in range(2, 6):
        _seed(f"c_{index}", f"第{index}个", 50 - index * 10)

    store.set_conversation_pinned("c_1", True)
    page = store.list_conversations(limit=2)
    assert len(page) == 2
    assert page[0]["conversation_id"] == "c_1"


def test_pin_does_not_touch_updated_at_and_is_idempotent(store):
    _seed("c_a", "甲", 10)
    store.set_conversation_pinned("c_a", True)

    session = DatabaseManager.get_session()
    try:
        row = ConversationRepository().get_by_conversation_id(session, "c_a")
        first_pinned_at = row.pinned_at
        updated_at = row.updated_at
    finally:
        session.close()

    assert first_pinned_at is not None
    # 置顶不是"活跃"，改写 updated_at 会让取消置顶后回不到原位
    assert updated_at == datetime(2026, 10, 22, 11, 50, 0)

    store.set_conversation_pinned("c_a", True)
    session = DatabaseManager.get_session()
    try:
        row = ConversationRepository().get_by_conversation_id(session, "c_a")
        assert row.pinned_at == first_pinned_at
    finally:
        session.close()


def test_pinned_item_exposes_pinned_at(store):
    _seed("c_p", "带置顶", 5)
    assert store.list_conversations(limit=1)[0]["pinned_at"] is None
    store.set_conversation_pinned("c_p", True)
    assert store.list_conversations(limit=1)[0]["pinned_at"] is not None


def test_pin_missing_conversation_returns_400():
    from app.api.conversations import pin_conversation
    from app.schemas.conversation import PinConversationRequest

    response = pin_conversation(PinConversationRequest(conversation_id="not_exist", pinned=True))
    assert response["code"] == 400
    assert "会话不存在" in response["message"]

    empty = pin_conversation(PinConversationRequest(conversation_id="  ", pinned=True))
    assert empty["code"] == 400
