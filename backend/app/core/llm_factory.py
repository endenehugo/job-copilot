"""统一 LLM 客户端工厂。

存在的理由（见 docs/2026-10-11-慢与静默失败-体验审计清单.md 的 A6）：
改造前全仓 13 处 `ChatTongyi(...)` 分散构造，无一处设置超时，
而 ChatTongyi 的 max_retries 默认为 10、DashScope 默认超时 300 秒，
单次调用最坏耗时可达 10 × 300 秒。同时各自 new 客户端也导致
HTTP 连接无法复用。

超时注入方式：ChatTongyi 自身没有 timeout 字段，但 model_kwargs 会
原样进入 `_default_params` 并透传给 `dashscope.Generation.call(**kwargs)`，
由 `api_request_factory` 取 `request_timeout` 用作 HTTP 超时（已实测验证）。

本工厂用 lru_cache 复用同参数客户端，兼顾连接复用与进程内单例。
"""

from __future__ import annotations

from functools import lru_cache

from langchain_community.chat_models import ChatTongyi

from app.core.config import settings


def _resolve_timeout(streaming: bool, timeout: int | None) -> int:
    if timeout is not None:
        return timeout
    # 流式场景下长回答本身就要时间，单独放宽
    return settings.llm_stream_timeout if streaming else settings.llm_request_timeout


@lru_cache(maxsize=64)
def build_chat_llm(
    model: str,
    temperature: float = 0.7,
    top_p: float | None = None,
    streaming: bool = False,
    timeout: int | None = None,
    max_retries: int | None = None,
) -> ChatTongyi:
    """构造（或复用）一个 ChatTongyi 客户端。

    Args:
        model: 模型名
        temperature: 采样温度
        top_p: 核采样参数；传 None 表示不设置，沿用服务端默认
        streaming: 是否主要用于流式调用，决定默认超时档位
        timeout: 单次请求超时秒数；None 时按 streaming 取配置值
        max_retries: 失败重试次数；None 时取 settings.llm_max_retries
    """
    kwargs: dict = {
        "model": model,
        "temperature": temperature,
        "streaming": streaming,
        "max_retries": max_retries if max_retries is not None else settings.llm_max_retries,
        "model_kwargs": {"request_timeout": _resolve_timeout(streaming, timeout)},
    }
    if top_p is not None:
        kwargs["top_p"] = top_p
    return ChatTongyi(**kwargs)
