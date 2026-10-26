"""后台历史压缩：把摘要生成挪出回答的关键路径。

为什么要异步：压缩是给**下一轮**铺路的——本轮回答要么用压缩后的历史（那就得等），
要么用全量历史（不等）。既然等待没有任何当下收益，就把它挪到响应之后做：
第 8 次请求不再付 2 秒压缩费，只多带 6 条历史（约 150 token）进回答。

并发约束（都写在这里，别散到调用方）：
- 单线程执行器：任务串行跑，同一会话不会同时压两次。
- pending 集合去重：同一会话已在队列里就不再提交，避免用户连点排队。
- 写库走 `update_context_summary` 的乐观锁（until_id 不前进就跳过）。即使多 worker
  部署让内存去重失效，也不会把水位写回去，最坏是多跑一次压缩（浪费一次调用）。
- 进程重启丢任务：幂等，下次请求会重新触发。

观测：任务跑在独立线程里，请求的 trace 可能已经结束了，所以不用 perf_trace 的 span，
改为单独打一行 `COMPRESS {json}`（同一个 app.perf 日志器）。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)
PERF_LOGGER = logging.getLogger("app.perf")

_executor: Optional[ThreadPoolExecutor] = None
_pending: set[str] = set()
_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            # 单线程：压缩是低优先级后台任务，串行跑既省并发额度，也天然避开同会话并发
            _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ctx-compress")
        return _executor


def pending_count() -> int:
    with _lock:
        return len(_pending)


def schedule(conversation_id: str) -> bool:
    """把该会话的压缩排进后台队列；已排队则跳过。返回是否真的提交。"""
    if not conversation_id:
        return False
    with _lock:
        if conversation_id in _pending:
            return False
        _pending.add(conversation_id)
    try:
        _get_executor().submit(_run, conversation_id)
    except RuntimeError:  # 解释器关闭中
        with _lock:
            _pending.discard(conversation_id)
        return False
    return True


def _run(conversation_id: str) -> None:
    started = time.perf_counter()
    written = False
    watermark = 0
    messages = 0
    error = None
    try:
        from app.core.deps import (
            get_context_compression_service,
            get_conversation_store_service,
        )

        store = get_conversation_store_service()
        compression = get_context_compression_service()
        # 重新读：任务排队期间可能又有新消息、或者别的进程已经压过一段
        old_summary, until_id = store.get_conversation_summary(conversation_id)
        history = store.get_messages_after(conversation_id, until_id)
        messages = len(history)
        if messages > settings.history_compress_trigger_messages:
            _, new_summary, watermark = compression.compress_history(
                history,
                old_summary=old_summary,
                max_messages=settings.history_compress_trigger_messages,
                target_messages=settings.history_compress_target_messages,
            )
            if watermark > 0 and new_summary:
                store.save_conversation_summary(conversation_id, new_summary, watermark)
                written = True
    except Exception as exc:  # 后台失败不能影响任何请求
        error = f"{type(exc).__name__}: {str(exc)[:120]}"
        logger.warning("后台压缩失败（会话 %s）：%s", conversation_id, error)
    finally:
        with _lock:
            _pending.discard(conversation_id)
        _emit(conversation_id, (time.perf_counter() - started) * 1000, messages, watermark, written, error)


def _emit(conversation_id: str, duration_ms: float, messages: int, watermark: int, written: bool, error) -> None:
    try:
        if not getattr(settings, "perf_trace_log", True):
            return
        payload = {
            "v": 1,
            "endpoint": "compress_background",
            "conversation_id": conversation_id,
            "messages": messages,
            "watermark": watermark,
            "written": written,
            "dur_ms": round(duration_ms, 1),
        }
        if error:
            payload["error"] = error
        PERF_LOGGER.info(
            "COMPRESS %s", json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
        )
    except Exception as exc:  # 日志失败同样不能影响业务
        logger.warning("后台压缩日志输出失败：%s", exc)


def reset_for_tests() -> None:
    """清空队列状态（单测用）。"""
    global _executor
    with _lock:
        _pending.clear()
    _executor = None
