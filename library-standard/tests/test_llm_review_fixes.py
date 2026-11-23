#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Regressions for the failure modes a hostile review of the protocol layers found."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from mas.library.standard.plugins.llm.anthropic import AnthropicLLMProvider
from mas.library.standard.plugins.llm.bedrock import BedrockLLMProvider
from mas.library.standard.plugins.llm.gemini import GeminiLLMProvider
from mas.library.standard.plugins.llm.openai import OpenAILLMProvider
from mas.library.standard.plugins.llm.vertex import VertexLLMProvider
from mas.runtime.engine.llm_reasoning import ReasoningSettings
from mas.runtime.reliability.classes import ClassifiedFailure, FailureClass


class _Transport:
    def __init__(
        self,
        body: dict[str, Any] | None = None,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        lines: list[str] | None = None,
    ) -> None:
        self.requests: list[httpx.Request] = []
        self.body = body or {}
        self.status = status
        self.headers = headers or {}
        self.lines = lines
        handler = httpx.MockTransport(self._answer)
        self.client = httpx.Client(transport=handler)
        self.aclient = httpx.AsyncClient(transport=handler)

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.lines is not None:
            return httpx.Response(self.status, content=("\n".join(self.lines) + "\n").encode(), headers=self.headers)
        return httpx.Response(self.status, json=self.body, headers=self.headers)

    def last(self) -> dict[str, Any]:
        return json.loads(self.requests[-1].content)


def _chat(provider: Any, **kwargs: Any) -> dict[str, Any]:
    return asyncio.run(provider.achat_completion(**kwargs))


def _wrap(cls: Any, transport: _Transport, base: str, **params: Any) -> Any:
    return cls.wrap(transport, {"api_base": base, "api_key": cls.default_secret, **params}, call_kinds=("llm",))


_USER = [{"role": "user", "content": "q"}]
_TOOLS = [{"type": "function", "function": {"name": "t", "parameters": {"type": "object"}}}]


def _tool_history(**tool: Any) -> list[dict[str, Any]]:
    return [
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c", "type": "function", "function": {"name": "t", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c", **tool},
    ]


# -- failures keep the server's delay and body -------------------------------------------


@pytest.mark.parametrize(
    ("cls", "base"),
    [
        (AnthropicLLMProvider, "http://127.0.0.1:9/v1"),
        (GeminiLLMProvider, "http://127.0.0.1:9/v1beta"),
        (BedrockLLMProvider, "http://127.0.0.1:9"),
        (OpenAILLMProvider, "http://127.0.0.1:9/v1"),
    ],
)
def test_a_429_keeps_the_servers_retry_after_on_the_failure(cls: Any, base: str) -> None:
    transport = _Transport({"error": {"message": "slow down"}}, status=429, headers={"retry-after": "7"})
    with pytest.raises(ClassifiedFailure) as raised:
        _chat(_wrap(cls, transport, base), model="m", messages=_USER)
    assert raised.value.failure_class is FailureClass.TRANSIENT
    assert raised.value.retry_after_s == 7.0


@pytest.mark.parametrize(
    ("cls", "base"),
    [
        (AnthropicLLMProvider, "http://127.0.0.1:9/v1"),
        (GeminiLLMProvider, "http://127.0.0.1:9/v1beta"),
        (OpenAILLMProvider, "http://127.0.0.1:9/v1"),
    ],
)
def test_a_streamed_400_is_classified_from_its_body(cls: Any, base: str) -> None:
    body = {"error": {"message": "prompt is too long: 300000 tokens > 200000 maximum context length"}}
    transport = _Transport(body, status=400)
    with pytest.raises(ClassifiedFailure) as raised:
        _chat(_wrap(cls, transport, base, stream=True), model="m", messages=_USER)
    assert raised.value.code == "CONTEXT_LENGTH_EXCEEDED"


# -- the chat-completions wire only gets chat-completions keys -----------------------------


def test_openai_wire_drops_provider_state_and_is_error_and_maps_retention() -> None:
    ok = {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}
    transport = _Transport(ok)
    messages = _tool_history(content="boom", is_error=True)
    messages[1]["provider_state"] = {"anthropic_thinking_blocks": [{"type": "thinking", "signature": "s"}]}
    for retention, wanted in (("5m", "in_memory"), ("in_memory", "in_memory"), ("1h", "24h"), ("24h", "24h")):
        _chat(
            _wrap(OpenAILLMProvider, transport, "http://127.0.0.1:9/v1"),
            model="gpt-4o",
            messages=messages,
            sampling={"prompt_cache_retention": retention},
        )
        body = transport.last()
        assert body["prompt_cache_retention"] == wanted
        assert all("provider_state" not in m and "is_error" not in m for m in body["messages"])
    assert messages[2]["is_error"] is True  # the caller's transcript is untouched


# -- bodies vendors reject ----------------------------------------------------------------


def test_anthropic_whitespace_blank_error_results_and_no_tool_choice_without_tools() -> None:
    transport = _Transport({"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"})
    messages = _tool_history(content="  ", is_error=True) + [
        {"role": "assistant", "content": "  \n"},
        {"role": "user", "content": " "},
        {"role": "user", "content": "again"},
    ]
    _chat(
        _wrap(AnthropicLLMProvider, transport, "http://127.0.0.1:9/v1"),
        model="claude-sonnet-4-6",
        messages=messages,
        tool_choice="required",
    )
    body = transport.last()
    assert "tool_choice" not in body
    result = body["messages"][2]["content"][0]
    assert result["is_error"] is True and result["content"] == "(no output)"
    flat = json.dumps(body["messages"])
    assert '"text": "  \\n"' not in flat and '"content": " "' not in flat


def test_anthropic_always_on_models_drop_sampling_and_forced_tool_choice_without_a_thinking_object() -> None:
    transport = _Transport({"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"})
    _chat(
        _wrap(AnthropicLLMProvider, transport, "http://127.0.0.1:9/v1"),
        model="claude-fable-5-2",
        messages=_USER,
        tools=_TOOLS,
        tool_choice="required",
        sampling={"temperature": 0.2},
        reasoning=ReasoningSettings(effort="high"),
    )
    body = transport.last()
    assert "thinking" not in body and "temperature" not in body and "tool_choice" not in body
    assert body["output_config"] == {"effort": "high"}


def test_always_on_models_replay_signed_blocks_even_when_effort_is_off() -> None:
    signed = [{"type": "thinking", "thinking": "x", "signature": "s"}]
    messages = _tool_history(content="r")
    messages[1]["provider_state"] = {"anthropic_thinking_blocks": signed}
    transport = _Transport({"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"})
    _chat(
        _wrap(AnthropicLLMProvider, transport, "http://127.0.0.1:9/v1"),
        model="claude-fable-5-2",
        messages=messages,
        tools=_TOOLS,
        reasoning=ReasoningSettings(effort="none"),
    )
    assert transport.last()["messages"][1]["content"][0] == signed[0]


def test_bedrock_blank_tool_results_and_truncated_tool_calls() -> None:
    ok = {"output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}}, "stopReason": "end_turn"}
    transport = _Transport(ok)
    _chat(
        _wrap(BedrockLLMProvider, transport, "http://127.0.0.1:9"),
        model="m",
        messages=_tool_history(content="  "),
        tools=_TOOLS,
    )
    result = transport.last()["messages"][2]["content"][0]["toolResult"]
    assert result["content"] == [{"text": "(no output)"}]

    cut = {
        "output": {"message": {"role": "assistant", "content": [{"toolUse": {"toolUseId": "u", "name": "t"}}]}},
        "stopReason": "max_tokens",
    }
    with pytest.raises(ClassifiedFailure) as raised:
        _chat(_wrap(BedrockLLMProvider, _Transport(cut), "http://127.0.0.1:9"), model="m", messages=_USER)
    assert raised.value.code == "TRUNCATED_TOOL_CALL"


def test_gemini_cached_content_replaces_system_tools_and_skips_blank_users() -> None:
    ok = {"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]}
    transport = _Transport(ok)
    _chat(
        _wrap(GeminiLLMProvider, transport, "http://127.0.0.1:9/v1beta"),
        model="gemini-2.5-pro",
        messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": " "}, *_USER],
        tools=_TOOLS,
        sampling={"prompt_cache_key": "cachedContents/abc"},
    )
    body = transport.last()
    assert body["cachedContent"] == "cachedContents/abc"
    assert "systemInstruction" not in body and "tools" not in body and "toolConfig" not in body
    assert body["contents"] == [{"role": "user", "parts": [{"text": "q"}]}]
    _chat(
        _wrap(GeminiLLMProvider, transport, "http://127.0.0.1:9/v1beta"),
        model="gemini-2.5-pro",
        messages=_USER,
        tools=_TOOLS,
        sampling={"prompt_cache_key": "projects/p/locations/l/cachedContents/abc"},
    )
    assert transport.last()["cachedContent"].startswith("projects/")


def test_gemini_sends_temperature_only_when_the_spec_sets_it() -> None:
    ok = {"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]}
    transport = _Transport(ok)
    provider = _wrap(GeminiLLMProvider, transport, "http://127.0.0.1:9/v1beta")
    _chat(provider, model="gemini-2.5-pro", messages=_USER, temperature=0.7)
    assert "temperature" not in transport.last()["generationConfig"]
    _chat(provider, model="gemini-2.5-pro", messages=_USER, temperature=0.7, sampling={"temperature": 0.2})
    assert transport.last()["generationConfig"]["temperature"] == 0.2


def test_gemini_finish_reasons_and_blocked_stream() -> None:
    bad = {"candidates": [{"content": {"parts": []}, "finishReason": "MALFORMED_FUNCTION_CALL"}]}
    with pytest.raises(ClassifiedFailure) as raised:
        _chat(_wrap(GeminiLLMProvider, _Transport(bad), "http://127.0.0.1:9/v1beta"), model="m", messages=_USER)
    assert raised.value.code == "MALFORMED_FUNCTION_CALL"
    filtered = {"candidates": [{"content": {"parts": []}, "finishReason": "PROHIBITED_CONTENT"}]}
    out = _chat(_wrap(GeminiLLMProvider, _Transport(filtered), "http://127.0.0.1:9/v1beta"), model="m", messages=_USER)
    assert out["finish_reason"] == "content_filter"
    lines = ['data: {"promptFeedback":{"blockReason":"SAFETY"}}']
    streamed = _chat(
        _wrap(GeminiLLMProvider, _Transport(lines=lines), "http://127.0.0.1:9/v1beta", stream=True),
        model="m",
        messages=_USER,
    )
    assert streamed["finish_reason"] == "content_filter"


def test_vertex_host_follows_the_location_only_for_the_default_host() -> None:
    ok = {"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]}
    default = VertexLLMProvider.default_api_base
    for location, host in (
        ("europe-west4", "europe-west4-aiplatform.googleapis.com"),
        ("global", "aiplatform.googleapis.com"),
    ):
        transport = _Transport(ok)
        provider = _wrap(VertexLLMProvider, transport, default, llm_proxy={"project": "p/x", "location": location})
        _chat(provider, model="gemini-2.5-pro", messages=_USER)
        url = transport.requests[-1].url
        assert url.host == host and b"/projects/p%2Fx/locations/" + location.encode() + b"/" in url.raw_path
    custom = _Transport(ok)
    _chat(
        _wrap(VertexLLMProvider, custom, "http://127.0.0.1:9", llm_proxy={"project": "p", "location": "europe-west4"}),
        model="gemini-2.5-pro",
        messages=_USER,
    )
    assert custom.requests[-1].url.host == "127.0.0.1"
