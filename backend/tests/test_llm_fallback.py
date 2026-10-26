"""FallbackChatModel 模型降级链单元测试。

不依赖网络：通过 patch 父类 ChatTongyi 的 _generate/_stream 模拟
"额度耗尽→切换模型"的真实报错路径（DashScope 400/401 错误经
check_response 转成 ValueError，code 在异常文本中）。
"""

from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import (
    ChatGeneration,
    ChatGenerationChunk,
    ChatResult,
)

from app.core.config import Settings, settings
from app.core.llm_factory import (
    CompatibleFallbackChatModel,
    FallbackChatModel,
    _is_quota_error,
    build_chat_llm,
)

QUOTA_MSG = (
    "request_id: req-test \n status_code: 400 \n "
    "code: AllocationQuota.FreeTierOnly \n message: 免费额度用完即停"
)


def _ok_result(text: str = "ok") -> ChatResult:
    return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])


@pytest.fixture(autouse=True)
def _clear_factory_cache():
    """build_chat_llm 是 lru_cache 单例，测试前后清空避免相互污染。"""
    build_chat_llm.cache_clear()
    yield
    build_chat_llm.cache_clear()


def _make_llm(**kwargs) -> FallbackChatModel:
    return FallbackChatModel(
        model="qwen-plus", temperature=0.1, max_retries=0, **kwargs
    )


class TestQuotaErrorDetection:
    def test_quota_codes_match(self):
        assert _is_quota_error(ValueError(QUOTA_MSG))
        assert _is_quota_error(ValueError("code: Arrearage message: 欠费"))
        assert _is_quota_error(ValueError("code: InvalidApiKey.NotFound"))
        assert _is_quota_error(ValueError("code: Model.NotExist"))

    def test_non_quota_errors_do_not_match(self):
        assert not _is_quota_error(ValueError("Connection refused"))
        # 限流走 max_retries 兜底，不触发粘性降级
        assert not _is_quota_error(ValueError("code: Throttling.RateQuota"))
        assert not _is_quota_error(RuntimeError("timeout"))


class TestModelChain:
    def test_chain_dedup_and_order(self):
        llm = _make_llm(
            fallback_models=["qwen-flash", "qwen-plus", "qwen-flash", ""]
        )
        assert llm._model_chain == ["qwen-plus", "qwen-flash"]

    def test_no_fallback_models(self):
        llm = _make_llm(fallback_models=[])
        assert llm._model_chain == ["qwen-plus"]


class TestGenerateFallback:
    def test_fallback_on_quota_error_and_sticky(self):
        calls = []

        def fake_generate(self, messages, stop=None, run_manager=None, **kwargs):
            calls.append(self.model_name)
            if self.model_name == "qwen-plus":
                raise ValueError(QUOTA_MSG)
            return _ok_result("small-model-answer")

        with patch(
            "langchain_community.chat_models.tongyi.ChatTongyi._generate",
            fake_generate,
        ):
            llm = _make_llm(fallback_models=["qwen-flash"])
            resp = llm.invoke("hi")

        assert calls == ["qwen-plus", "qwen-flash"]
        assert resp.content == "small-model-answer"
        # 粘性切换：额度耗尽不会自愈，后续调用继续用降级模型
        assert llm.model_name == "qwen-flash"

    def test_non_quota_error_propagates(self):
        def fake_generate(self, messages, stop=None, run_manager=None, **kwargs):
            raise ValueError("Connection refused")

        with patch(
            "langchain_community.chat_models.tongyi.ChatTongyi._generate",
            fake_generate,
        ):
            llm = _make_llm(fallback_models=["qwen-flash"])
            with pytest.raises(ValueError, match="Connection refused"):
                llm.invoke("hi")

    def test_all_models_exhausted_raises_last_error(self):
        def fake_generate(self, messages, stop=None, run_manager=None, **kwargs):
            raise ValueError(f"code: Arrearage model={self.model_name}")

        with patch(
            "langchain_community.chat_models.tongyi.ChatTongyi._generate",
            fake_generate,
        ):
            llm = _make_llm(fallback_models=["qwen-flash"])
            with pytest.raises(ValueError, match="model=qwen-flash"):
                llm.invoke("hi")


class TestStreamFallback:
    def test_fallback_before_first_chunk(self):
        def fake_stream(self, messages, stop=None, run_manager=None, **kwargs):
            if self.model_name == "qwen-plus":
                raise ValueError(QUOTA_MSG)
            for text in ["a", "b"]:
                yield ChatGenerationChunk(message=AIMessageChunk(content=text))

        with patch(
            "langchain_community.chat_models.tongyi.ChatTongyi._stream",
            fake_stream,
        ):
            llm = _make_llm(fallback_models=["qwen-flash"])
            chunks = [c.content for c in llm.stream("hi")]

        assert chunks == ["a", "b"]
        assert llm.model_name == "qwen-flash"

    def test_mid_stream_error_propagates(self):
        """已产出内容后再报错无法降级（客户端已收到部分输出），原样抛出。"""

        def fake_stream(self, messages, stop=None, run_manager=None, **kwargs):
            yield ChatGenerationChunk(message=AIMessageChunk(content="partial"))
            raise ValueError(QUOTA_MSG)

        with patch(
            "langchain_community.chat_models.tongyi.ChatTongyi._stream",
            fake_stream,
        ):
            llm = _make_llm(fallback_models=["qwen-flash"])
            received = []
            with pytest.raises(ValueError, match="FreeTierOnly"):
                for chunk in llm.stream("hi"):
                    received.append(chunk.content)

        assert received == ["partial"]


class TestFactoryIntegration:
    def test_build_chat_llm_returns_fallback_model(self):
        llm = build_chat_llm("qwen-plus")
        assert isinstance(llm, FallbackChatModel)
        expected = [
            m
            for m in settings.llm_fallback_models
            if m != "qwen-plus" and m not in settings.llm_compatible_mode_models
        ]
        assert llm.fallback_models == expected

    def test_factory_cache_still_works(self):
        assert build_chat_llm("qwen-plus") is build_chat_llm("qwen-plus")

    def test_primary_filtered_from_fallback_list(self):
        llm = build_chat_llm(settings.llm_model)
        assert settings.llm_model not in llm.fallback_models
        assert llm._model_chain[0] == settings.llm_model

    def test_defaults_come_from_selectable_models(self):
        """默认主/小模型必须出自「可更换的模型」清单，降级链也只能取清单内的模型。

        用 _env_file=None 只看代码默认值：用户用 .env 覆盖 LLM_MODEL 时
        不该被这条断言拦住（那是显式选择，不是手滑写错模型 ID）。
        """
        defaults = Settings(_env_file=None)
        assert defaults.llm_selectable_models, "可更换清单不能为空"
        assert defaults.llm_model in defaults.llm_selectable_models
        assert defaults.llm_small_model in defaults.llm_selectable_models
        assert set(defaults.llm_fallback_models) <= set(defaults.llm_selectable_models)
        assert len(set(defaults.llm_fallback_models)) == len(defaults.llm_fallback_models)


class TestCompatibleModeRouting:
    """端点分流：这一代新模型走兼容模式，老模型留在原生 ChatTongyi。"""

    @pytest.fixture(autouse=True)
    def _fake_api_key(self, monkeypatch):
        # 兼容模式客户端构造时会校验 api_key，测试环境可能没有 .env
        monkeypatch.setattr(settings, "dashscope_api_key", "sk-test", raising=False)

    def test_new_model_uses_compatible_client(self):
        llm = build_chat_llm("qwen3.8-max")
        assert isinstance(llm, CompatibleFallbackChatModel)
        assert str(llm.openai_api_base).rstrip("/") == settings.llm_compatible_base_url

    def test_legacy_model_stays_on_native_client(self):
        # 默认降级链里含新模型，但主模型是老模型时不应被拖去兼容模式
        llm = build_chat_llm("qwen-plus")
        assert isinstance(llm, FallbackChatModel)
        assert not isinstance(llm, CompatibleFallbackChatModel)

    def test_native_chain_drops_compatible_models(self):
        llm = build_chat_llm("qwen-vl-plus")
        assert not isinstance(llm, CompatibleFallbackChatModel)
        assert "qwen3.8-max" not in llm.fallback_models
        assert "qwen3.8-flash" not in llm.fallback_models

    def test_compatible_chain_keeps_legacy_models(self):
        llm = build_chat_llm("qwen3.8-max")
        assert "qwen-plus" in llm.fallback_models

    def test_kimi_k3_sampling_params_are_fixed(self):
        """kimi-k3 只接受 temperature=1 且不支持 top_p（实测 400）。"""
        llm = build_chat_llm("kimi-k3", temperature=0.8, top_p=0.7)
        assert llm.temperature == 1.0
        assert llm.top_p is None

    def test_switch_restores_temperature_after_kimi(self):
        llm = build_chat_llm("qwen3.8-max", temperature=0.3)
        llm._switch_to("kimi-k3")
        assert llm.temperature == 1.0
        llm._switch_to("qwen3.8-flash")
        assert llm.temperature == 0.3

    def test_defaults_come_from_selectable_models(self):
        """默认主/小模型必须出自「可更换的模型」清单，降级链也只能取清单内的模型。

        用 _env_file=None 只看代码默认值：用户用 .env 覆盖 LLM_MODEL 时
        不该被这条断言拦住（那是显式选择，不是手滑写错模型 ID）。
        """
        defaults = Settings(_env_file=None)
        assert defaults.llm_selectable_models, "可更换清单不能为空"
        assert defaults.llm_model in defaults.llm_selectable_models
        assert defaults.llm_small_model in defaults.llm_selectable_models
        assert set(defaults.llm_fallback_models) <= set(defaults.llm_selectable_models)
        assert len(set(defaults.llm_fallback_models)) == len(defaults.llm_fallback_models)


class TestThinkingSwitch:
    """思考开关：结构化判断类调用要能显式关掉，且降级到不认识该参数的模型时不能 400。

    实测同一段面试评价 prompt：开思考 15.8–19.7s / 888 tokens，关思考 3.67s / 159 tokens，
    评价长度与评分档位不变（见 docs/2026-10-18-模拟面试提速方案.md）。
    """

    @pytest.fixture(autouse=True)
    def _fake_api_key(self, monkeypatch):
        monkeypatch.setattr(settings, "dashscope_api_key", "sk-test", raising=False)

    def test_thinking_off_goes_into_extra_body(self):
        llm = build_chat_llm("qwen3.8-flash", enable_thinking=False)
        assert llm.extra_body == {"enable_thinking": False}

    def test_thinking_unset_keeps_server_default(self):
        llm = build_chat_llm("qwen3.8-flash")
        assert llm.extra_body is None

    def test_thinking_is_part_of_cache_key(self):
        """lru_cache 必须区分开关：否则先建的无思考客户端会被后一次复用，开关静默失效。"""
        off = build_chat_llm("qwen3.8-flash", enable_thinking=False)
        on = build_chat_llm("qwen3.8-flash")
        assert off is not on
        assert off.extra_body == {"enable_thinking": False}
        assert on.extra_body is None

    def test_fallback_drops_unsupported_thinking_param(self):
        """降级链上有 deepseek/kimi/qwen-plus，给它们传 enable_thinking 有 400 风险。"""
        llm = build_chat_llm("qwen3.8-flash", enable_thinking=False)
        llm._switch_to("qwen-plus")
        assert llm.extra_body is None
        llm._switch_to("qwen3.8-flash")
        assert llm.extra_body == {"enable_thinking": False}

    def test_max_tokens_on_compatible_client(self):
        llm = build_chat_llm("qwen3.8-flash", max_tokens=600)
        assert llm.max_tokens == 600

    def test_max_tokens_on_native_client_goes_to_model_kwargs(self):
        # ChatTongyi 没有 max_tokens 字段，只能经 model_kwargs 透传给 dashscope
        llm = build_chat_llm("qwen-plus", max_tokens=600)
        assert llm.model_kwargs["max_tokens"] == 600
        assert llm.model_kwargs["request_timeout"] == settings.llm_request_timeout

    def test_native_client_ignores_thinking(self):
        # 原生链路不认这个参数，忽略即可，不能因此构造失败
        llm = build_chat_llm("qwen-plus", enable_thinking=False)
        assert isinstance(llm, FallbackChatModel)
        assert not hasattr(llm, "extra_body")


class TestPerfTraceSingleRecord:
    """streaming=True 的客户端在 invoke() 时 ChatOpenAI._generate 内部会走 _stream，
    两层都记录会把一次调用算成两次（线上实测 token 数一模一样的两条）。"""

    @staticmethod
    def _patch_stream_inside_generate():
        def fake_stream(self, messages, stop=None, run_manager=None, **kwargs):
            yield ChatGenerationChunk(message=AIMessageChunk(content="ok"))

        def fake_generate(self, messages, stop=None, run_manager=None, **kwargs):
            chunks = list(self._stream(messages))
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content="".join(c.message.content for c in chunks)))]
            )

        return (
            patch("langchain_community.chat_models.tongyi.ChatTongyi._generate", fake_generate),
            patch("langchain_community.chat_models.tongyi.ChatTongyi._stream", fake_stream),
        )

    def test_generate_delegating_to_stream_records_one_call(self):
        from app.core import perf_trace as pt

        llm = _make_llm()
        generate_patch, stream_patch = self._patch_stream_inside_generate()
        with generate_patch, stream_patch:
            with pt.trace_scope("probe") as trace:
                llm.invoke("hi")
        calls = [span for span in trace.spans if span["name"] == "llm.call"]
        assert len(calls) == 1

    def test_direct_stream_still_records_one_call(self):
        from app.core import perf_trace as pt

        def fake_stream(self, messages, stop=None, run_manager=None, **kwargs):
            yield ChatGenerationChunk(message=AIMessageChunk(content="ok"))

        llm = _make_llm()
        with patch("langchain_community.chat_models.tongyi.ChatTongyi._stream", fake_stream):
            with pt.trace_scope("probe") as trace:
                chunks = list(llm.stream("hi"))
        calls = [span for span in trace.spans if span["name"] == "llm.call"]
        assert len(chunks) == 1 and len(calls) == 1
