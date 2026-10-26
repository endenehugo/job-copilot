"""请求级分段计时：给一次 chat 请求记录每个阶段（数据库、压缩、检索、LLM、工具、校验…）的耗时。

用法：API 入口 `with trace_scope("chat", ...)` 开启；业务代码里 `with span("名字")` 包裹要计时的片段。
没有开启 trace 的请求（其他接口、单元测试）里 `span` 是空操作，对现有行为零影响。

约束：本模块任何函数都不能让业务请求失败——内部异常一律吞掉并记一条 warning；
业务代码自己的异常照常向上传播（span 只在 attrs 里记 error=异常类型名）。
不记录问题和回答的文本。
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)
PERF_LOGGER = logging.getLogger("app.perf")
LLM_SPAN = "llm.call"
SCHEMA_VERSION = 1

_current: ContextVar[Optional["PerfTrace"]] = ContextVar("perf_trace_current", default=None)
# 栈元素是 (seq, name)；用元组保证每个 context 看到的是不可变快照
_stack: ContextVar[tuple] = ContextVar("perf_trace_stack", default=())


def _now_ms() -> float:
    return time.perf_counter() * 1000.0


def _stage_key(name: str) -> str:
    """阶段名取第一个 "." 之前的部分（db.save → db），并限定为响应头安全字符。"""
    return re.sub(r"[^a-z0-9_]", "_", name.split(".", 1)[0].lower()) or "other"


class PerfTrace:
    def __init__(self, kind: str, attrs: dict[str, Any]) -> None:
        self.trace_id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.attrs: dict[str, Any] = dict(attrs)
        self.spans: list[dict] = []
        self._t0 = _now_ms()
        self._end: Optional[float] = None
        self._lock = threading.Lock()

    # ---- 记录 ----
    def _open(self, name: str, attrs: dict[str, Any], parent: Optional[tuple]) -> dict:
        with self._lock:
            entry = {
                "seq": len(self.spans) + 1,
                "name": name,
                "parent": parent[1] if parent else None,
                "pseq": parent[0] if parent else None,
                "start_ms": round(_now_ms() - self._t0, 3),
                "dur_ms": None,
                "attrs": dict(attrs),
            }
            self.spans.append(entry)
            return entry

    def _close(self, entry: dict) -> None:
        entry["dur_ms"] = round(max(0.0, (_now_ms() - self._t0) - entry["start_ms"]), 3)

    def _finish(self) -> None:
        self._end = _now_ms()

    # ---- 汇总 ----
    def total_ms(self) -> float:
        return (self._end if self._end is not None else _now_ms()) - self._t0

    def _split_by_nesting(self) -> tuple[dict[str, float], dict[str, float]]:
        """把 span 分成 (顶层阶段, 被包裹的子阶段)。

        顶层阶段之间不重叠，相加不会超过 total；被别的 span 包住的（如 retrieve 里的
        rerank、builtin）落到子阶段。llm.call 两边都不进——它被调用的阶段包含，
        单独用 llm_summary 统计。
        """
        by_seq = {s["seq"]: s for s in self.spans}
        top: dict[str, float] = {}
        sub: dict[str, float] = {}
        for span_entry in self.spans:
            if span_entry["name"] == LLM_SPAN or span_entry["dur_ms"] is None:
                continue
            key = _stage_key(span_entry["name"])
            nested_in_same = False
            has_other_owner = False
            ancestor = by_seq.get(span_entry["pseq"])
            while ancestor is not None:
                if ancestor["name"] != LLM_SPAN:
                    if _stage_key(ancestor["name"]) == key:
                        # retrieve.conversation 之于 retrieve：同一个阶段，不该单独出现
                        nested_in_same = True
                        break
                    has_other_owner = True
                ancestor = by_seq.get(ancestor["pseq"])
            if nested_in_same:
                continue
            target = sub if has_other_owner else top
            target[key] = target.get(key, 0.0) + span_entry["dur_ms"]
        return top, sub

    def stages(self) -> dict[str, float]:
        """顶层阶段名 → 毫秒（互不重叠，相加 <= total_ms）。"""
        return self._split_by_nesting()[0]

    def substages(self) -> dict[str, float]:
        """被其他阶段包住的子阶段合计（如 rerank、retrieve.builtin），与某个顶层阶段重叠。"""
        return self._split_by_nesting()[1]

    def llm_summary(self) -> tuple[int, float]:
        calls = [s for s in self.spans if s["name"] == LLM_SPAN]
        return len(calls), sum(s["dur_ms"] or 0.0 for s in calls)

    def to_log_dict(self) -> dict:
        calls, llm_ms = self.llm_summary()
        stages = self.stages()
        data: dict[str, Any] = {
            "v": SCHEMA_VERSION,
            "trace_id": self.trace_id,
            "endpoint": self.kind,
            **self.attrs,
            "total_ms": round(self.total_ms(), 1),
            "stages": {key: round(value, 1) for key, value in stages.items()},
            "substages": {key: round(value, 1) for key, value in self.substages().items()},
            # 顶层阶段合计：明显小于 total 说明有时间花在没打点的地方，加起来反超 total 说明汇总有 bug
            "sum_stages_ms": round(sum(stages.values()), 1),
            "llm_calls": calls,
            "llm_ms": round(llm_ms, 1),
            "spans": self.spans,
        }
        return data

    def to_server_timing(self) -> str:
        """W3C Server-Timing：顶层阶段互不重叠；子阶段带 desc=of:<父阶段> 标注重叠关系；
        llm 是跨阶段的合计（与 compress/rerank/answer 等重叠），不是一个独立阶段。"""
        parts = [f"total;dur={self.total_ms():.1f}"]
        parts += [f"{key};dur={value:.1f}" for key, value in self.stages().items()]
        parts += [f'{key};dur={value:.1f};desc="of:sub"' for key, value in self.substages().items()]
        calls, llm_ms = self.llm_summary()
        if calls:
            parts.append(f'llm;dur={llm_ms:.1f};desc="n={calls}"')
        return ", ".join(parts)

    def elapsed_ms(self) -> float:
        """从 trace 起点到现在的毫秒数，用于流式里标 ttft/done 的位置。"""
        return self.total_ms()


def current_trace() -> Optional[PerfTrace]:
    return _current.get()


def _emit_log(trace: PerfTrace) -> None:
    try:
        if not getattr(settings, "perf_trace_log", True):
            return
        PERF_LOGGER.info("PERF %s", json.dumps(trace.to_log_dict(), ensure_ascii=False, separators=(",", ":"), default=str))
    except Exception as exc:  # 日志失败不能影响业务
        logger.warning("perf trace 日志输出失败：%s", exc)


@contextmanager
def trace_scope(kind: str, **attrs: Any) -> Iterator[PerfTrace]:
    """在请求入口开启计时；退出时输出一行 PERF 日志。已有 trace 时复用外层的，不嵌套。"""
    existing = _current.get()
    if existing is not None:
        yield existing
        return
    trace = PerfTrace(kind, attrs)
    token = _current.set(trace)
    stack_token = _stack.set(())
    try:
        yield trace
    finally:
        try:
            trace._finish()
        except Exception as exc:  # 计时自身的失败不能影响业务请求
            logger.warning("perf trace 收尾失败：%s", exc)
        for var, tok in ((_stack, stack_token), (_current, token)):
            try:
                var.reset(tok)
            except ValueError:  # 流式生成器跨 context 迭代时 token 可能不属于当前 context
                var.set(() if var is _stack else None)
        _emit_log(trace)


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[Optional[dict]]:
    """给一个代码片段计时。没有 trace 时 yield None 且什么都不记。"""
    trace = _current.get()
    if trace is None:
        yield None
        return
    entry = None
    token = None
    try:
        stack = _stack.get()
        entry = trace._open(name, attrs, stack[-1] if stack else None)
        token = _stack.set(stack + ((entry["seq"], name),))
    except Exception as exc:
        logger.warning("perf span 打开失败：%s", exc)
    try:
        yield entry
    except BaseException as exc:
        if entry is not None:
            entry["attrs"]["error"] = type(exc).__name__
        raise
    finally:
        try:
            if entry is not None:
                trace._close(entry)
            if token is not None:
                _stack.reset(token)
        except Exception as exc:
            logger.warning("perf span 关闭失败：%s", exc)


def record_span(name: str, dur_ms: float, **attrs: Any) -> None:
    """登记一个已经测好耗时的片段（流式生成器里不能跨 yield 持有 span，用它代替）。"""
    trace = _current.get()
    if trace is None:
        return
    try:
        stack = _stack.get()
        entry = trace._open(name, attrs, stack[-1] if stack else None)
        entry["start_ms"] = round(max(0.0, entry["start_ms"] - dur_ms), 3)
        entry["dur_ms"] = round(dur_ms, 3)
    except Exception as exc:
        logger.warning("perf record_span 失败：%s", exc)


def record_llm_call(
    model: str,
    dur_ms: float,
    *,
    in_tokens: Optional[int] = None,
    out_tokens: Optional[int] = None,
    fallback: bool = False,
    error: Optional[str] = None,
) -> None:
    """记录一次 LLM 调用；parent 取当前 span，用来区分压缩、重排、回答、工具决策、校验等。"""
    attrs: dict[str, Any] = {"model": model, "fallback": fallback}
    if in_tokens is not None:
        attrs["in_tokens"] = in_tokens
    if out_tokens is not None:
        attrs["out_tokens"] = out_tokens
    if error is not None:
        attrs["error"] = error
    record_span(LLM_SPAN, dur_ms, **attrs)


def set_attr(key: str, value: Any) -> None:
    trace = _current.get()
    if trace is not None:
        trace.attrs[key] = value


def configure_perf_logging() -> None:
    """给 app.perf 日志器加一个输出到 stdout 的处理器（systemd 会把它收进 journal）。幂等。

    不改根日志器，也不改 propagate；应用本身没有请求级日志配置，所以必须单独开一个口子。
    开关只由 _emit_log 里的 settings.perf_trace_log 决定：这里把 disabled 固定为 False，
    否则启动时关过一次，之后动态打开也不会再输出。
    """
    PERF_LOGGER.setLevel(logging.INFO)
    PERF_LOGGER.disabled = False
    if any(getattr(handler, "_perf_handler", False) for handler in PERF_LOGGER.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler._perf_handler = True  # type: ignore[attr-defined]
    PERF_LOGGER.addHandler(handler)
