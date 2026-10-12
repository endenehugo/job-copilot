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

模型降级链（FallbackChatModel）：
百炼免费额度按模型独立发放且耗尽后服务端不会自动切换（官方 FAQ 明确
要求手动改 model 参数）。本工厂在客户端补齐：调用命中额度耗尽/欠费/
模型不可用类错误时，按 settings.llm_fallback_models 依次切换模型重试。
切换通过改写 model_name 实现，在进程内粘性生效——额度耗尽不会自愈，
粘住降级模型是预期行为。

双客户端（2026-10-12 实测后新增）：
百炼把这一代新模型（qwen3.x/3.8、deepseek-*、kimi-*）挂在原生
multimodal-generation 端点，而 langchain_community 的 ChatTongyi 用一份内置
白名单决定端点（只认含 "vl" 的模型名），把它们发到 text-generation 端点会
报 `400 InvalidParameter: url error`。因此凡是命中
settings.llm_compatible_mode_models 的模型（或降级链上出现这类模型）都改走
百炼 OpenAI 兼容模式（ChatOpenAI + /compatible-mode/v1）——该端点对老模型
同样可用，所以同一条降级链可以在一个客户端内自由跨模型切换。
老模型继续走 ChatTongyi 原生链路，不动现网已验证的问答/流式路径。

线程安全说明：降级切换发生在 lru_cache 返回的共享实例上，并发场景
下两个请求可能同时触发切换，最坏结果是都切到同一个降级模型（策略
漂移而非输出错误），可接受；降级本身是低频路径。
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any, Iterator, List, Optional

from langchain_community.chat_models import ChatTongyi
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

# 判定为"换模型才有意义"的错误标记。额度/权限类错误重试同一模型只会
# 原样再失败一次，而限流（Throttling）类错误已有 max_retries 兜底，
# 不在降级之列，避免瞬时抖动导致粘性降级。
_QUOTA_ERROR_MARKERS = (
    "AllocationQuota",  # 免费额度用完即停（AllocationQuota.FreeTierOnly）
    "FreeTierOnly",
    "Arrearage",  # 欠费
    "QuotaExhausted",
    "InvalidApiKey",  # Key 失效/未开通该模型
    "AccessDenied",  # 未开通模型访问权限
    "Model.NotFound",
    "Model.NotExist",
)


def _is_quota_error(exc: BaseException) -> bool:
    """DashScope 的 400/401 错误经 check_response 转成 ValueError、
    其余转成 HTTPError，code 都在异常文本里，按标记做子串匹配即可。"""
    text = str(exc)
    return any(marker in text for marker in _QUOTA_ERROR_MARKERS)


def _apply_sampling_fixes(llm: Any, model: str) -> None:
    """按模型改写采样参数（第三方模型的参数限制，见 settings 里的说明）。

    在构造时和降级切换时都要调用：降级链上不同模型的参数约束可能不同，
    只改 model_name 会把主模型的 temperature 带过去，直接 400。

    只对 ChatOpenAI 生效：ChatTongyi 根本没有 temperature 字段（pydantic
    默认忽略未知入参，所以调用方传的 temperature 在原生链路上是空转），
    而受这些限制的第三方模型只会走兼容模式。
    """
    if not isinstance(llm, ChatOpenAI):
        return
    fixed_temperature = settings.llm_fixed_temperature_models.get(model)
    if fixed_temperature is not None:
        llm.temperature = fixed_temperature
    elif getattr(llm, "base_temperature", None) is not None:
        # 切回普通模型时还原构造时的温度，避免被上一个模型的约束污染
        llm.temperature = llm.base_temperature
    if model in set(settings.llm_models_without_top_p):
        # ChatOpenAI 的 _default_params 会把 None 值整项剔除，所以置 None 即不下传。
        llm.top_p = None


class QuotaFallbackMixin:
    """额度错误自动降级链，与具体客户端解耦（ChatTongyi / ChatOpenAI 通用）。

    只放方法、不放字段：pydantic 字段由各具体子类自己声明，避免混入类
    干扰 pydantic 的字段收集。
    """

    @property
    def _model_chain(self) -> List[str]:
        """主模型 + 去重后的降级模型序列。"""
        chain = [self.model_name]
        for model in self.fallback_models:
            if model and model not in chain:
                chain.append(model)
        return chain

    def _switch_to(self, model: str) -> None:
        # ChatTongyi 与 ChatOpenAI 的 _default_params/_invocation_params 都读
        # model_name（字段别名都是 "model"），改写它即可让后续调用（含
        # bind_tools 的绑定调用）走新模型。
        self.model_name = model
        _apply_sampling_fixes(self, model)
        logger.warning("LLM 降级切换：当前使用模型 %s", model)

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        chain = self._model_chain
        for idx, model in enumerate(chain):
            if self.model_name != model:
                self._switch_to(model)
            try:
                return super()._generate(
                    messages, stop=stop, run_manager=run_manager, **kwargs
                )
            except Exception as exc:
                if idx < len(chain) - 1 and _is_quota_error(exc):
                    logger.warning(
                        "模型 %s 调用失败（疑似额度/权限问题），尝试降级：%s",
                        model,
                        exc,
                    )
                    continue
                raise

    def _stream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        chain = self._model_chain
        for idx, model in enumerate(chain):
            if self.model_name != model:
                self._switch_to(model)
            iterator = super()._stream(
                messages, stop=stop, run_manager=run_manager, **kwargs
            )
            try:
                # DashScope 的错误响应在流的首个分片前抛出，缓冲首个分片
                # 即可在"零输出"阶段安全降级；一旦已产出内容则不再切换。
                first_chunk = next(iterator)
            except StopIteration:
                return
            except Exception as exc:
                if idx < len(chain) - 1 and _is_quota_error(exc):
                    logger.warning(
                        "模型 %s 流式调用失败（疑似额度/权限问题），尝试降级：%s",
                        model,
                        exc,
                    )
                    continue
                raise
            yield first_chunk
            yield from iterator
            return


class FallbackChatModel(QuotaFallbackMixin, ChatTongyi):
    """老一代模型用：带额度错误自动降级链的 ChatTongyi。

    正常路径零额外开销：只在捕获到额度类异常时才走切换逻辑。
    """

    fallback_models: List[str] = []


class CompatibleFallbackChatModel(QuotaFallbackMixin, ChatOpenAI):
    """本代新模型用：同一套降级链，但走百炼 OpenAI 兼容模式端点。

    这些模型在原生 text-generation 端点会报 `url error`（见模块 docstring）。
    兼容模式对老模型同样可用，因此降级链里混排新老模型也不会切换失败。
    """

    fallback_models: List[str] = []

    # 构造时的采样温度：切到 kimi-k3 这类只接受 temperature=1 的模型时会临时改写，
    # 再切回普通模型时需要还原，否则温度被上一个模型的约束污染。
    base_temperature: Optional[float] = None


def _resolve_timeout(streaming: bool, timeout: int | None) -> int:
    if timeout is not None:
        return timeout
    # 流式场景下长回答本身就要时间，单独放宽
    return settings.llm_stream_timeout if streaming else settings.llm_request_timeout


def _needs_compatible_mode(model: str) -> bool:
    """该模型是否必须走百炼 OpenAI 兼容模式端点。"""
    return bool(model) and model in set(settings.llm_compatible_mode_models)


def _resolve_api_key() -> str:
    key = settings.dashscope_api_key or os.environ.get("DASHSCOPE_API_KEY") or ""
    if not key:
        raise ValueError(
            "兼容模式模型需要 DashScope API Key：请在 .env 或环境变量里配置 DASHSCOPE_API_KEY"
        )
    return key


@lru_cache(maxsize=64)
def build_chat_llm(
    model: str,
    temperature: float = 0.7,
    top_p: float | None = None,
    streaming: bool = False,
    timeout: int | None = None,
    max_retries: int | None = None,
) -> ChatTongyi:
    """构造（或复用）一个带降级链的 LLM 客户端。

    客户端类型按模型自动选择：
    - 命中 settings.llm_compatible_mode_models（或降级链上出现这类模型）
      → `CompatibleFallbackChatModel`（ChatOpenAI + 百炼兼容模式端点）；
    - 其余 → `FallbackChatModel`（ChatTongyi 原生端点，保持现网行为）。

    Args:
        model: 主模型名
        temperature: 采样温度
        top_p: 核采样参数；传 None 表示不设置，沿用服务端默认
        streaming: 是否主要用于流式调用，决定默认超时档位
        timeout: 单次请求超时秒数；None 时按 streaming 取配置值
        max_retries: 失败重试次数；None 时取 settings.llm_max_retries
    """
    fallbacks = [m for m in settings.llm_fallback_models if m != model]
    resolved_timeout = _resolve_timeout(streaming, timeout)
    resolved_retries = (
        max_retries if max_retries is not None else settings.llm_max_retries
    )

    if not _needs_compatible_mode(model):
        # 原生 ChatTongyi 只能调老模型：把这一代新模型从降级链里剔掉。
        # 放进链上也只会在切换时 400（url error 不在额度错误标记里，不会继续降级）。
        fallbacks = [m for m in fallbacks if not _needs_compatible_mode(m)]

    kwargs: dict = {
        "model": model,
        "temperature": temperature,
        "streaming": streaming,
        "max_retries": resolved_retries,
        "fallback_models": fallbacks,
    }
    if top_p is not None:
        kwargs["top_p"] = top_p

    if _needs_compatible_mode(model):
        # 兼容模式对老模型同样可用，所以链上混排新老模型都不会切换失败。
        llm: Any = CompatibleFallbackChatModel(
            api_key=_resolve_api_key(),
            base_url=settings.llm_compatible_base_url,
            timeout=resolved_timeout,
            base_temperature=temperature,
            **kwargs,
        )
    else:
        llm = FallbackChatModel(
            model_kwargs={"request_timeout": resolved_timeout},
            **kwargs,
        )
    # 采样参数按模型修正（例如 kimi-k3 只接受 temperature=1 且不支持 top_p）
    _apply_sampling_fixes(llm, model)
    return llm
