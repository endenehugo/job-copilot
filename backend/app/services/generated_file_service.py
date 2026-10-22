"""交付文件的归属登记与查询。

下载路由 `/api/v1/conversation/file/<filename>` 只拿得到文件名，
归属信息必须由生成侧在落盘时登记（见 `repositories/generated_file_repository.py`
里关于"为什么不用签名 URL"的说明）。

三条约定：
- 登记失败**不影响生成**：文件已经落盘，最坏结果是这份文件只有管理员能下载，
  比为了一个索引写不进去而让用户丢掉已经算好的简历要好。
- 查不到登记 = 本功能上线前的旧文件，只有管理员能取（与 legacy 会话语义一致）。
- 删除会话时顺手清掉登记与物理文件，避免 `generated_docs` 只增不减。
"""

from __future__ import annotations

import logging
import os

from app.repositories import DatabaseManager, GeneratedFileRepository

logger = logging.getLogger(__name__)


def register_generated_file(filename: str, conversation_id: str) -> None:
    """登记归属。可以在事务内调用，也可以在事务外调用。

    不能在这里无条件 `with session.begin()`：`DatabaseManager` 用的是
    scoped_session（线程内共享），`load_generation` 重建 HTML 时外层已经开着事务，
    再 begin() 会抛 `InvalidRequestError: A transaction is already begun`。
    同理**这里不能 close 也不能 rollback**——那样会把调用方的事务一起废掉。

    失败只告警：文件已经落盘，最坏结果是这份文件只有管理员能下载，
    比为了一个登记行让用户丢掉算好的简历要好。
    """
    if not filename or not conversation_id:
        return
    session = DatabaseManager.get_session()
    try:
        if session.in_transaction():
            GeneratedFileRepository.record(session, filename, conversation_id)
        else:
            with session.begin():
                GeneratedFileRepository.record(session, filename, conversation_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "文件归属登记失败（文件已落盘，将只有管理员可下载）：%s / %s",
            filename,
            conversation_id,
            exc_info=exc,
        )


def lookup_generated_file(filename: str) -> str | None:
    """返回归属的 conversation_id；未登记返回 None。"""
    session = DatabaseManager.get_session()
    try:
        entity = GeneratedFileRepository.get(session, filename)
        return entity.conversation_id if entity is not None else None
    finally:
        session.close()


def delete_generated_files(conversation_id: str) -> list[str]:
    """清掉该会话的登记，返回文件名列表供调用方删除物理文件。"""
    session = DatabaseManager.get_session()
    try:
        with session.begin():
            filenames = GeneratedFileRepository.list_by_conversation_id(session, conversation_id)
            GeneratedFileRepository.delete_by_conversation_id(session, conversation_id)
        return filenames
    finally:
        session.close()


def unlink_generated_files(filenames: list[str], resource_dir: str) -> int:
    """删物理文件，返回成功删除的数量。目录不存在、文件已被清理都算正常。"""
    removed = 0
    for name in filenames:
        # 名字来自数据库，仍然做一次目录约束：防的是历史脏数据里出现 ../ 之类
        path = os.path.realpath(os.path.join(resource_dir, name))
        if not path.startswith(os.path.realpath(resource_dir) + os.sep):
            continue
        try:
            if os.path.isfile(path):
                os.remove(path)
                removed += 1
        except OSError as exc:
            logger.warning("交付文件删除失败：%s（%s）", path, exc)
    return removed
