"""会话归属：身份取值、可见范围、越权判定。

`conversations.owner_key` 有四类取值：

| 值 | 含义 | 访客可见 | 管理员可见 |
| --- | --- | --- | --- |
| `""`（LEGACY_OWNER） | 归属功能上线前的历史会话 | 否 | 是 |
| `"*"`（ADMIN_OWNER） | 管理员自建 | 否 | 是 |
| `"demo"`（DEMO_OWNER） | 预置演示样本 | 是，只读 | 是 |
| `"v_xxx"` | 某个访客 | 仅自己 | 是 |

**空串与 `*` 的区别不是风格问题**：空串是"没有归属"，任何访客令牌都不会等于它，
所以老数据天然被隔离、不需要迁移；`*` 是"管理员视角"的哨兵值，只在查询时作为
"不过滤"的信号存在，永远不会写进数据库。

**越权与不存在必须返回同一种错误**。错误信息本身就是一次存在性探测：
如果"无权访问"和"会话不存在"文案不同，攻击者用一个不存在的 ID 对照一下就能
判断哪些 ID 是真的。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from fastapi import Depends, Request

from app.core.security import get_current_role

# 管理员视角哨兵：查库时代表"不过滤"
ADMIN_OWNER = "*"
# 归属功能上线前的历史数据；空串永远不匹配任何访客
LEGACY_OWNER = ""
# 预置演示样本：所有访客可见，写操作一律拒绝
DEMO_OWNER = "demo"
# 管理员自建会话落库时用的归属值。与 ADMIN_OWNER 分开是有意的：
# `*` 是查询侧的"不过滤"哨兵，不该出现在数据里；这里是真实的归属标记
ADMIN_OWNER_KEY = "admin"

# 访客令牌的 cookie 名。改名前先想清楚：改一次等于所有在线访客换个身份、丢掉自己的会话
VISITOR_COOKIE_NAME = "jc_visitor"
# 180 天。访客态本来就临时，但半年足够覆盖"面试官看完过几天又来试"的场景
VISITOR_COOKIE_MAX_AGE = 180 * 24 * 3600

# 不存在与越权共用这一句
NOT_FOUND_MESSAGE = "会话不存在"
READONLY_MESSAGE = "演示会话只读，请新建对话后使用"

# 访客标识的形态约束：签发时是 `v_` + 24 位 hex。
# 校验一次可以挡掉把别人 uid 手工塞进 cookie 的尝试（虽然签名本来也挡得住）
VISITOR_PREFIX = "v_"


def get_owner_key(request: Request, role: str = Depends(get_current_role)) -> str:
    """当前请求的归属标识。管理员返回哨兵值，其余返回中间件签发的访客 ID。"""
    if role == "admin":
        return ADMIN_OWNER
    # 中间件正常运行时一定有值；取不到时返回空串——空串什么都不匹配，
    # 失败方向是"看不到"而不是"看得到全部"，这是有意的
    return getattr(request.state, "owner_key", "") or ""


# ---------------------------------------------------------------------------
# 请求作用域：ConversationChatService 的调用链很深（chat_stream → _prepare_chat
# → 会话校验 / 取详情 / 取文档），逐层透传 owner 会漏。改用 ContextVar，与
# model_scope 的 request_conversation_scope 同一套做法。
#
# **必须在路由层、且流式生成器内部开启**：生成器在响应阶段才被消费，
# 在端点函数里 with 一下就退出，等于没生效。
# ---------------------------------------------------------------------------
_current_owner_key: ContextVar[str] = ContextVar("current_owner_key", default="")


@contextmanager
def request_owner_scope(owner_key: str):
    token = _current_owner_key.set(owner_key or "")
    try:
        yield
    finally:
        _current_owner_key.reset(token)


def current_owner_key() -> str:
    """当前请求的归属标识；未开作用域时返回空串，即什么都看不到。"""
    return _current_owner_key.get()


def can_access(conversation_owner: str, viewer: str) -> bool:
    """viewer 能否看到 owner_key 为 conversation_owner 的会话。"""
    if viewer == ADMIN_OWNER:
        return True
    if not viewer or viewer == LEGACY_OWNER:
        # 没有身份（中间件未生效等）：什么都不给
        return False
    if conversation_owner == DEMO_OWNER:
        return True
    return conversation_owner == viewer


def is_readonly(conversation_owner: str, viewer: str) -> bool:
    """演示样本对访客只读；管理员不受限（他要能维护样本）。"""
    return conversation_owner == DEMO_OWNER and viewer != ADMIN_OWNER


def storage_owner_key(viewer: str) -> str:
    """写入数据库时该用哪个归属值。

    管理员的请求归到 `admin` 而不是哨兵 `*`，这样"你自己聊出来的会话"
    与"访客聊出来的"在库里是两种真实取值，列表里也一眼分得清。
    """
    return ADMIN_OWNER_KEY if viewer == ADMIN_OWNER else viewer


def visible_owner_keys(viewer: str) -> list[str] | None:
    """列表查询的归属条件；None 表示不过滤（管理员）。"""
    if viewer == ADMIN_OWNER:
        return None
    return [viewer, DEMO_OWNER]


def check_owner(
    session, conversation_id: str, owner_key: str, *, not_found_message: str | None = None
) -> str:
    """在**调用方的 session 上**校验归属，返回归属值。

    单独拆出来是因为 `Session.close()` 会把外部正在进行的事务一起回滚：
    在 `with session.begin():` 内部再调一个"自己开 session 自己关"的校验函数，
    表现是删除/更新偶尔静默不生效。事务内一律用这个版本。

    `not_found_message` 供"按子资源 id 直取"的端点使用：这类端点对
    **不存在**回的是「版本不存在」/「分析记录不存在」，那越权就必须回同一句，
    否则两句话的差异本身就是一次存在性探测。
    """
    from app.repositories import ConversationRepository

    entity = ConversationRepository().get_by_conversation_id(session, conversation_id)
    owner = entity.owner_key if entity is not None else None
    if owner is None or not can_access(owner, owner_key):
        raise ValueError(not_found_message or NOT_FOUND_MESSAGE)
    return owner


def assert_child_owner(
    session, conversation_id: str, owner_key: str, message: str, *, code: int = 404
) -> str:
    """按子资源 id 直取的端点专用。

    这类端点的"不存在"分支都写成 `fail(message, code=404)`，所以越权也必须
    回**同一句文案 + 同一个 code**。只把文案对齐不够——状态码的差异同样能
    用来判断某个 id 是否真实存在。
    """
    from app.core.exceptions import ApiError

    try:
        return check_owner(session, conversation_id, owner_key)
    except ValueError:
        raise ApiError(message, code) from None


def require_writable(owner: str, owner_key: str) -> None:
    """在已确认可见的前提下再判只读。管理员的写操作不受限。"""
    if is_readonly(owner, owner_key):
        raise PermissionError(READONLY_MESSAGE)


def assert_conversation_owner(conversation_id: str, owner_key: str) -> str:
    """第二层授权：会话存在且当前身份可见，否则抛与外键校验一致的 ValueError。

    返回归属值，供调用方判断是否只读，免得为了读 owner 再查一次库。
    只用于 handler 顶部这类"不在事务里"的位置；事务内改用 check_owner。
    """
    from app.repositories import DatabaseManager

    session = DatabaseManager.get_session()
    try:
        return check_owner(session, conversation_id, owner_key)
    finally:
        session.close()


def assert_writable(conversation_id: str, owner_key: str) -> str:
    """在 assert_conversation_owner 之上再加只读判断，写操作统一用它。"""
    owner = assert_conversation_owner(conversation_id, owner_key)
    require_writable(owner, owner_key)
    return owner
