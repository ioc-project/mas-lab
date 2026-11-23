#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Prompt-cache fields, catalog-gated sampling and normalized usage across the LLM protocol layers."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from mas.library.standard.plugins.llm.anthropic import AnthropicLLMProvider
from mas.library.standard.plugins.llm.azure import AzureLLMProvider
from mas.library.standard.plugins.llm.bedrock import BedrockLLMProvider
from mas.library.standard.plugins.llm.compatible import (
    GroqLLMProvider,
    LiteLLMLLMProvider,
    OpenAICompatibleLLMProvider,
    OpenRouterLLMProvider,
)
from mas.library.standard.plugins.llm.gemini import GeminiLLMProvider
from mas.library.standard.plugins.llm.http_protocol import HttpLlmProtocol, normalize_usage
from mas.library.standard.plugins.llm.openai import OpenAILLMProvider
from mas.library.standard.plugins.llm.vertex import VertexLLMProvider

_USER = [{"role": "user", "content": "q"}]
_TOOL = {"type": "function", "function": {"name": "t", "parameters": {"type": "object"}}}


class _Transport:
    def __init__(self, body: dict[str, Any] | None = None, lines: list[str] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.body = body or {}
        self.lines = lines
        handler = httpx.MockTransport(self._answer)
        self.client = httpx.Client(transport=handler)
        self.aclient = httpx.AsyncClient(transport=handler)

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.lines is not None:
            return httpx.Response(200, content=("\n".join(self.lines) + "\n").encode())
        return httpx.Response(200, json=self.body)

    @property
    def sent(self) -> dict[str, Any]:
        return json.loads(self.requests[-1].content)


def _run(cls: type[HttpLlmProtocol], transport: _Transport, **call: Any) -> dict[str, Any]:
    params = {"api_base": "http://127.0.0.1:9/v1", "project": "p", "api_key": cls.default_secret}
    provider = cls.wrap(transport, params, call_kinds=("llm",))
    call.setdefault("messages", _USER)
    return asyncio.run(provider.achat_completion(**call))


_ANTHROPIC_OK = {
    "content": [{"type": "text", "text": "ok"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 4, "output_tokens": 2},
}


def _anthropic(**call: Any) -> dict[str, Any]:
    transport = _Transport(_ANTHROPIC_OK)
    _run(AnthropicLLMProvider, transport, **call)
    return transport.sent


_CONVERSATION = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "one"},
    {"role": "assistant", "content": "ok"},
    {"role": "user", "content": "two"},
    {"role": "assistant", "content": "ok"},
    {"role": "user", "content": "three"},
]


def _marks(body: dict[str, Any]) -> tuple[bool, bool, list[bool]]:
    system = isinstance(body.get("system"), list) and "cache_control" in body["system"][0]
    tools = any("cache_control" in t for t in body.get("tools", []))
    msgs = [
        m["role"] == "user" and isinstance(m["content"], list) and "cache_control" in m["content"][-1]
        for m in body["messages"]
    ]
    return system, tools, msgs


# -- Anthropic ---------------------------------------------------------------------------------


def test_anthropic_marks_system_tools_and_two_user_turns_by_default() -> None:
    body = _anthropic(model="unlisted", tools=[_TOOL], messages=_CONVERSATION)
    assert _marks(body) == (True, True, [False, False, True, False, True])
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_false_sends_no_marker() -> None:
    body = _anthropic(
        model="unlisted", tools=[_TOOL], messages=_CONVERSATION, sampling={"prompt_cache_breakpoints": False}
    )
    assert _marks(body) == (False, False, [False] * 5)


def test_anthropic_object_picks_each_part() -> None:
    sampling = {"prompt_cache_breakpoints": {"system": True, "messages": 1}}
    body = _anthropic(model="unlisted", tools=[_TOOL], messages=_CONVERSATION, sampling=sampling)
    assert _marks(body) == (True, False, [False, False, False, False, True])
    sampling = {"prompt_cache_breakpoints": {"tools": True, "messages": 0}}
    body = _anthropic(model="unlisted", tools=[_TOOL], messages=_CONVERSATION, sampling=sampling)
    assert _marks(body) == (False, True, [False] * 5)


def test_anthropic_never_sends_more_than_four_markers() -> None:
    body = _anthropic(model="unlisted", tools=[_TOOL], messages=_CONVERSATION)
    assert json.dumps(body).count("cache_control") == 4


@pytest.mark.parametrize(
    ("retention", "mark"),
    [
        ("in_memory", {"type": "ephemeral"}),
        ("5m", {"type": "ephemeral"}),
        ("1h", {"type": "ephemeral", "ttl": "1h"}),
        ("24h", {"type": "ephemeral", "ttl": "1h"}),
    ],
)
def test_anthropic_retention_sets_the_marker_ttl(retention: str, mark: dict[str, Any]) -> None:
    body = _anthropic(model="claude-opus-5", messages=_CONVERSATION, sampling={"prompt_cache_retention": retention})
    assert body["system"][0]["cache_control"] == mark
    assert body["messages"][-1]["content"][-1]["cache_control"] == mark


def test_anthropic_model_the_catalog_marks_unsupported_gets_no_marker_and_no_temperature() -> None:
    body = _anthropic(model="gpt-4o", messages=_CONVERSATION, sampling={"temperature": 0.2, "top_p": 0.5})
    assert _marks(body) == (False, False, [False] * 5)
    assert body["temperature"] == 0.2 and body["top_p"] == 0.5


def test_anthropic_catalog_unsupported_sampling_is_dropped() -> None:
    sampling = {"temperature": 0.2, "top_p": 0.5, "top_k": 4}
    for model in ("claude-opus-5", "anthropic/claude-sonnet-5-5", "anthropic.claude-fable-5-1"):
        body = _anthropic(model=model, messages=_USER, sampling=sampling)
        assert not {"temperature", "top_p", "top_k"} & set(body)


def test_anthropic_sampling_allowed_model_and_unknown_model_send_explicit_values() -> None:
    sampling = {"temperature": 0.3, "top_p": 0.9, "top_k": 7}
    for model in ("claude-haiku-4-5", "claude-sonnet-4-6", "unlisted"):
        body = _anthropic(model=model, messages=_USER, sampling=sampling)
        assert (body["temperature"], body["top_p"], body["top_k"]) == (0.3, 0.9, 7)


def test_anthropic_call_default_temperature_is_not_sent() -> None:
    assert "temperature" not in _anthropic(model="unlisted", messages=_USER, temperature=0.7)
    assert "temperature" not in _anthropic(model="unlisted", messages=_USER, sampling={"top_p": 0.5})


def test_anthropic_usage_reports_cache_read_and_write() -> None:
    usage = {"input_tokens": 3, "output_tokens": 2, "cache_creation_input_tokens": 10, "cache_read_input_tokens": 100}
    out = _run(AnthropicLLMProvider, _Transport({**_ANTHROPIC_OK, "usage": usage}), model="m")
    assert out["usage"]["prompt_tokens"] == 113
    assert out["usage"]["cached_tokens"] == 100
    assert out["usage"]["cache_write_tokens"] == 10


def test_anthropic_stream_usage_reports_cache_read_and_write() -> None:
    lines = [
        'data: {"type":"message_start","message":{"usage":{"input_tokens":3,"output_tokens":1,'
        '"cache_creation_input_tokens":10,"cache_read_input_tokens":100}}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"x"}}',
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":5}}',
    ]
    out = _run(AnthropicLLMProvider, _Transport(lines=lines), model="m", stream=True)
    assert out["usage"]["cached_tokens"] == 100
    assert out["usage"]["cache_write_tokens"] == 10
    assert out["usage"]["completion_tokens"] == 5


# -- Bedrock -----------------------------------------------------------------------------------

_BEDROCK_OK = {
    "output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}},
    "stopReason": "end_turn",
    "usage": {"inputTokens": 4, "outputTokens": 2},
}
_POINT = {"cachePoint": {"type": "default"}}


def _bedrock(body: dict[str, Any] | None = None, **call: Any) -> dict[str, Any]:
    transport = _Transport(body or _BEDROCK_OK)
    _run(BedrockLLMProvider, transport, **call)
    return transport.sent


def test_bedrock_known_model_gets_cache_points_after_system_tools_and_last_users() -> None:
    body = _bedrock(model="anthropic.claude-opus-5", tools=[_TOOL], messages=_CONVERSATION)
    assert body["system"] == [{"text": "sys"}, _POINT]
    assert body["toolConfig"]["tools"][-1] == _POINT
    assert [m["content"][-1] == _POINT for m in body["messages"]] == [False, False, True, False, True]
    assert json.dumps(body).count("cachePoint") == 4


def test_bedrock_false_object_and_unknown_model() -> None:
    sampling = {"prompt_cache_breakpoints": False}
    body = _bedrock(model="anthropic.claude-opus-5", tools=[_TOOL], messages=_CONVERSATION, sampling=sampling)
    assert "cachePoint" not in json.dumps(body)
    unknown = _bedrock(model="us.meta.llama", tools=[_TOOL], messages=_CONVERSATION)
    assert "cachePoint" not in json.dumps(unknown)
    sampling = {"prompt_cache_breakpoints": {"system": True}}
    picked = _bedrock(model="us.meta.llama", tools=[_TOOL], messages=_CONVERSATION, sampling=sampling)
    assert picked["system"][-1] == _POINT and json.dumps(picked).count("cachePoint") == 1


@pytest.mark.parametrize(("retention", "ttl"), [("in_memory", "5m"), ("5m", "5m"), ("1h", "1h"), ("24h", "1h")])
def test_bedrock_retention_sets_ttl(retention: str, ttl: str) -> None:
    body = _bedrock(model="anthropic.claude-opus-5", messages=_USER, sampling={"prompt_cache_retention": retention})
    assert body["messages"][-1]["content"][-1] == {"cachePoint": {"type": "default", "ttl": ttl}}


def test_bedrock_temperature_follows_sampling_and_catalog() -> None:
    sampling = {"temperature": 0.4}
    assert "inferenceConfig" not in _bedrock(model="anthropic.claude-opus-5", sampling=sampling)
    assert "inferenceConfig" not in _bedrock(model="unlisted", temperature=0.7)
    assert _bedrock(model="unlisted", sampling=sampling)["inferenceConfig"]["temperature"] == 0.4
    assert _bedrock(model="anthropic.claude-haiku-4-5", sampling=sampling)["inferenceConfig"]["temperature"] == 0.4


def test_bedrock_usage_reports_cache_read_and_write_both_modes() -> None:
    usage = {"inputTokens": 3, "outputTokens": 2, "cacheReadInputTokens": 100, "cacheWriteInputTokens": 10}
    for stream in (False, True):
        out = _run(BedrockLLMProvider, _Transport({**_BEDROCK_OK, "usage": usage}), model="m", stream=stream)
        assert out["usage"]["cached_tokens"] == 100
        assert out["usage"]["cache_write_tokens"] == 10
        assert out["usage"]["prompt_tokens"] == 113
        assert out["usage"]["total_tokens"] == 115


# -- Gemini and Vertex -------------------------------------------------------------------------

_GEMINI_USAGE = {
    "promptTokenCount": 8,
    "candidatesTokenCount": 2,
    "totalTokenCount": 15,
    "cachedContentTokenCount": 6,
    "thoughtsTokenCount": 5,
}
_GEMINI_OK = {
    "candidates": [{"content": {"role": "model", "parts": [{"text": "ok"}]}, "finishReason": "STOP"}],
    "usageMetadata": _GEMINI_USAGE,
}


@pytest.mark.parametrize("cls", [GeminiLLMProvider, VertexLLMProvider])
def test_gemini_family_sends_a_cached_content_name_only(cls: type[HttpLlmProtocol]) -> None:
    transport = _Transport(_GEMINI_OK)
    _run(cls, transport, model="gemini-2.5-pro", sampling={"prompt_cache_key": "cachedContents/abc"})
    assert transport.sent["cachedContent"] == "cachedContents/abc"
    for sampling in (
        {"prompt_cache_key": "my-key"},
        {"prompt_cache_retention": "24h"},
        {"prompt_cache_breakpoints": {}},
    ):
        transport = _Transport(_GEMINI_OK)
        _run(cls, transport, model="gemini-2.5-pro", sampling=sampling)
        assert "cachedContent" not in transport.sent and "prompt_cache_key" not in transport.sent


@pytest.mark.parametrize("cls", [GeminiLLMProvider, VertexLLMProvider])
def test_gemini_family_usage_reports_cached_and_reasoning_tokens(cls: type[HttpLlmProtocol]) -> None:
    out = _run(cls, _Transport(_GEMINI_OK), model="m")
    assert out["usage"]["cached_tokens"] == 6 and out["usage"]["reasoning_tokens"] == 5
    lines = ["data: " + json.dumps({**_GEMINI_OK, "candidates": _GEMINI_OK["candidates"]})]
    streamed = _run(cls, _Transport(lines=lines), model="m", stream=True)
    assert streamed["usage"]["cached_tokens"] == 6 and streamed["usage"]["reasoning_tokens"] == 5


# -- OpenAI family -----------------------------------------------------------------------------

_OPENAI_USAGE = {
    "prompt_tokens": 20,
    "completion_tokens": 9,
    "total_tokens": 29,
    "prompt_tokens_details": {"cached_tokens": 12},
    "completion_tokens_details": {"reasoning_tokens": 4},
}
_OPENAI_OK = {
    "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
    "usage": _OPENAI_USAGE,
}
_CACHE_SAMPLING = {
    "prompt_cache_key": "k1",
    "prompt_cache_retention": "24h",
    "prompt_cache_breakpoints": {"system": True},
}


def _openai(cls: type[HttpLlmProtocol], **call: Any) -> dict[str, Any]:
    transport = _Transport(_OPENAI_OK)
    _run(cls, transport, **call)
    return transport.sent


@pytest.mark.parametrize("cls", [OpenAILLMProvider, AzureLLMProvider, OpenRouterLLMProvider, LiteLLMLLMProvider])
def test_cache_key_and_retention_reach_the_body_but_breakpoints_never(cls: type[HttpLlmProtocol]) -> None:
    body = _openai(cls, model="gpt-4o", sampling=_CACHE_SAMPLING)
    assert body["prompt_cache_key"] == "k1" and body["prompt_cache_retention"] == "24h"
    assert "prompt_cache_breakpoints" not in body


def test_hosts_that_reject_unknown_fields_do_not_get_cache_fields() -> None:
    assert OpenAICompatibleLLMProvider.forwards_prompt_cache_fields is False
    assert OpenAILLMProvider.forwards_prompt_cache_fields is True
    assert OpenRouterLLMProvider.forwards_prompt_cache_fields is True
    assert LiteLLMLLMProvider.forwards_prompt_cache_fields is True
    body = _openai(GroqLLMProvider, model="gpt-4o", sampling=_CACHE_SAMPLING)
    assert not {"prompt_cache_key", "prompt_cache_retention", "prompt_cache_breakpoints"} & set(body)


def test_openai_usage_reports_cached_and_reasoning_tokens_both_modes() -> None:
    out = _run(OpenAILLMProvider, _Transport(_OPENAI_OK), model="gpt-4o")
    assert out["usage"]["cached_tokens"] == 12 and out["usage"]["reasoning_tokens"] == 4
    assert out["usage"]["prompt_tokens"] == 20
    chunk = {"choices": [{"delta": {"content": "x"}, "finish_reason": "stop"}], "usage": _OPENAI_USAGE}
    streamed = _run(OpenAILLMProvider, _Transport(lines=["data: " + json.dumps(chunk), "data: [DONE]"]), stream=True)
    assert streamed["usage"]["cached_tokens"] == 12 and streamed["usage"]["reasoning_tokens"] == 4


# -- shared helper -----------------------------------------------------------------------------


def test_normalize_usage_maps_every_wire_spelling_and_keeps_the_original_keys() -> None:
    assert normalize_usage(None) == {}
    assert normalize_usage({"prompt_tokens": 1}) == {"prompt_tokens": 1}
    out = normalize_usage(
        {"input_tokens_details": {"cached_tokens": 3}, "output_tokens_details": {"reasoning_tokens": 2}}
    )
    assert out["cached_tokens"] == 3 and out["reasoning_tokens"] == 2
    assert "input_tokens_details" in out
    out = normalize_usage({"cache_read_input_tokens": 0, "cache_creation_input_tokens": 7})
    assert out["cached_tokens"] == 0 and out["cache_write_tokens"] == 7
