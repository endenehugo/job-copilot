"""百炼兼容模式的思考内容透传。

背景：langchain-openai 0.3.x 的 `_convert_delta_to_message_chunk` 只认
content / tool_calls / audio，`delta.reasoning_content` 会被直接丢弃。qwen3 系
默认开思考、思考期间 content 为空，不补这一步上层就完全看不到那几十秒在干什么
（线上实测首字 81 秒）。这里锁住补丁本身，防止升级依赖后静默失效。
"""

from __future__ import annotations

from langchain_core.messages import AIMessageChunk

from app.core.llm_factory import CompatibleFallbackChatModel


def _client() -> CompatibleFallbackChatModel:
    # 不真发请求，api_key 只要能被 ChatOpenAI 接受即可
    return CompatibleFallbackChatModel(
        api_key="sk-test",
        base_url="https://example.invalid/v1",
        model="qwen3.8-flash",
    )


def test_reasoning_content_is_kept_in_additional_kwargs():
    llm = _client()
    chunk = {"choices": [{"delta": {"content": "", "reasoning_content": "用户在问 SSE"}}]}

    generation = llm._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, None)

    assert generation is not None
    assert generation.message.additional_kwargs["reasoning_content"] == "用户在问 SSE"
    assert generation.message.content == ""


def test_content_and_reasoning_can_share_a_chunk():
    llm = _client()
    chunk = {"choices": [{"delta": {"content": "答案", "reasoning_content": "想好了"}}]}

    generation = llm._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, None)

    assert generation.message.content == "答案"
    assert generation.message.additional_kwargs["reasoning_content"] == "想好了"


def test_chunk_without_reasoning_is_untouched():
    """没有思考字段时不能凭空塞一个空串，否则上层会推空帧。"""
    llm = _client()
    chunk = {"choices": [{"delta": {"content": "答案"}}]}

    generation = llm._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, None)

    assert generation.message.content == "答案"
    assert "reasoning_content" not in generation.message.additional_kwargs
