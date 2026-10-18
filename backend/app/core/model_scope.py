"""请求级模型传递：ContextVar 方案。

为什么用 ContextVar 而不是显式传参：
简历评分 / JD 解析 / 项目优化 / 模拟面试同时也是 chat Agent 的工具
（bind 在 agent_llm 上），model 参数穿不进工具内部调用链。
在 API 入口把"本次请求的模型"写入 ContextVar，各服务构建客户端时读取，
能同时覆盖直接入口与工具内调用，侵入最小。

流式链路注意：SSE 的生成器在响应阶段才被消费，API 层必须在生成器内部
开启 scope（见 conversations.py 的 generate()），在端点函数里 with 一下
就退出是没有意义的——生成器体执行时 scope 已经复位。

多线程说明：同步生成器由 starlette 经 anyio 线程池消费，
anyio.to_thread.run_sync 会拷贝当前 context，ContextVar 值可见；
每个 ASGI 请求运行在独立 Task（独立 context 拷贝）中，不会跨请求泄漏。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from app.core.config import settings
from app.core.model_policy import resolve_model

_current_model: ContextVar[str] = ContextVar("current_request_model", default="")


@contextmanager
def request_model_scope(requested: str | None, role: str):
    """解析并绑定本次请求的模型；退出时复位。

    resolve_model 抛 ValueError（越权模型）发生在进入作用域时，
    非流式端点可借此在调用业务服务前返回 400。
    """
    model = resolve_model(role, requested)
    token = _current_model.set(model)
    try:
        yield model
    finally:
        _current_model.reset(token)


def current_chat_model() -> str:
    """服务层取主模型：请求作用域内返回所选模型，作用域外回落全局默认。"""
    return _current_model.get() or settings.llm_model
