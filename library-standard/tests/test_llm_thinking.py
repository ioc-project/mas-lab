#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Thinking requests, signed-state replay and tool error flags across the protocol layers."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from mas.library.standard.plugins.llm.anthropic import AnthropicLLMProvider
from mas.library.standard.plugins.llm.bedrock import BedrockLLMProvider
from mas.library.standard.plugins.llm.gemini import GeminiLLMProvider
from mas.library.standard.plugins.llm.thinking import (
    anthropic_thinking,
    gemini_thinking_config,
    loop_replayable,
    thinking_mode,
    thinking_off,
)
from mas.runtime.engine.llm_reasoning import ReasoningSettings


class _Transport:
    def __init__(self, body: dict[str, Any] | None = None, *, lines: list[str] | None = None) -> None:
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

    def last(self) -> dict[str, Any]:
        return json.loads(self.requests[-1].content)


def _chat(provider: Any, **kwargs: Any) -> dict[str, Any]:
    return asyncio.run(provider.achat_completion(**kwargs))


def _anthropic(transport: _Transport, **params: Any) -> AnthropicLLMProvider:
    return AnthropicLLMProvider.wrap(
        transport,
        {"api_base": "http://127.0.0.1:9/v1", "api_key": "k", "api_key_env": "K", **params},
        call_kinds=("llm",),
    )


def _gemini(transport: _Transport, **params: Any) -> GeminiLLMProvider:
    return GeminiLLMProvider.wrap(
        transport,
        {"api_base": "http://127.0.0.1:9/v1beta", "api_key": "k", "api_key_env": "K", **params},
        call_kinds=("llm",),
    )


def _bedrock(transport: _Transport, **params: Any) -> BedrockLLMProvider:
    return BedrockLLMProvider.wrap(
        transport,
        {"api_base": "http://127.0.0.1:9", "api_key": "k", "api_key_env": "K", **params},
        call_kinds=("llm",),
    )


_TOOLS = [{"type": "function", "function": {"name": "t", "parameters": {"type": "object"}}}]
_SIGNED = [{"type": "thinking", "thinking": "hm", "signature": "sig-1"}, {"type": "redacted_thinking", "data": "xx"}]


def _tool_loop(state: dict[str, Any] | None) -> list[dict[str, Any]]:
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{}"}}],
    }
    if state is not None:
        assistant["provider_state"] = state
    return [{"role": "user", "content": "q"}, assistant, {"role": "tool", "tool_call_id": "c1", "content": "r"}]


# -- the shared mapping -------------------------------------------------------------------


def test_thinking_mode_comes_from_the_catalog_then_the_spec() -> None:
    assert thinking_mode("claude-opus-5-5", ReasoningSettings()) == "adaptive"
    assert thinking_mode("claude-fable-5-2", ReasoningSettings()) == "always_on"
    assert thinking_mode("claude-haiku-4-5", ReasoningSettings()) == "budget"
    assert thinking_mode("some-new-model", ReasoningSettings(effort="low")) == "adaptive"
    assert thinking_mode("some-new-model", ReasoningSettings(budget_tokens=2000)) == "budget"


def test_adaptive_budget_always_on_and_off() -> None:
    adaptive = anthropic_thinking("claude-opus-5-5", ReasoningSettings(effort="minimal"), max_tokens=8000)
    assert adaptive.thinking == {"type": "adaptive"} and adaptive.effort == "low"
    shown = anthropic_thinking("claude-opus-5-5", ReasoningSettings(effort="high", exclude=False), max_tokens=8000)
    assert shown.thinking == {"type": "adaptive", "display": "summarized"} and shown.effort == "high"
    always = anthropic_thinking("claude-fable-5-2", ReasoningSettings(effort="max"), max_tokens=8000)
    assert always.thinking is None and always.effort == "max" and not always.active
    budget = anthropic_thinking("claude-haiku-4-5", ReasoningSettings(effort="medium"), max_tokens=20000)
    assert budget.thinking == {"type": "enabled", "budget_tokens": 10000}
    clamped = anthropic_thinking("claude-haiku-4-5", ReasoningSettings(budget_tokens=9000), max_tokens=4000)
    assert clamped.thinking == {"type": "enabled", "budget_tokens": 2976}
    too_small = anthropic_thinking("claude-haiku-4-5", ReasoningSettings(budget_tokens=9000), max_tokens=2000)
    assert too_small.thinking is None
    off = anthropic_thinking("claude-opus-5-5", ReasoningSettings(effort="none"), max_tokens=8000)
    assert off.thinking is None and off.effort is None
    assert thinking_off(ReasoningSettings(effort="disable")) and thinking_off(ReasoningSettings(think=False))
    assert not thinking_off(ReasoningSettings(effort="low"))


def test_gemini_thinking_config_by_mode() -> None:
    assert gemini_thinking_config("gemini-2.5-pro", ReasoningSettings(budget_tokens=2048)) == {"thinkingBudget": 2048}
    assert gemini_thinking_config("gemini-2.5-pro", ReasoningSettings(budget_tokens=999999)) == {
        "thinkingBudget": 32768
    }
    assert gemini_thinking_config("gemini-2.5-pro", ReasoningSettings(effort="low")) == {"thinkingBudget": 4000}
    assert gemini_thinking_config("gemini-2.5-flash", ReasoningSettings(effort="none")) == {"thinkingBudget": 0}
    assert gemini_thinking_config("gemini-2.5-pro", ReasoningSettings(effort="none")) is None
    assert gemini_thinking_config("gemini-3-pro-preview", ReasoningSettings(effort="medium")) == {
        "thinkingLevel": "low"
    }
    assert gemini_thinking_config("gemini-3-pro-preview", ReasoningSettings(effort="high", exclude=False)) == {
        "thinkingLevel": "high",
        "includeThoughts": True,
    }
    assert gemini_thinking_config("gemini-3-pro-preview", ReasoningSettings()) is None


def test_loop_replayable_looks_at_the_current_loop_only() -> None:
    assert loop_replayable([{"role": "user", "content": "q"}], "k")
    assert not loop_replayable(_tool_loop(None), "k")
    assert loop_replayable(_tool_loop({"k": [1]}), "k")
    older = _tool_loop(None) + [{"role": "assistant", "content": "done"}, {"role": "user", "content": "next"}]
    assert loop_replayable(older, "k")


# -- anthropic ----------------------------------------------------------------------------

_ANTHROPIC_OK = {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}


def test_anthropic_sends_adaptive_thinking_effort_and_drops_sampling_and_forced_tools() -> None:
    transport = _Transport(_ANTHROPIC_OK)
    _chat(
        _anthropic(transport),
        model="claude-sonnet-4-6",
        messages=[{"role": "user", "content": "q"}],
        tools=_TOOLS,
        tool_choice="required",
        reasoning=ReasoningSettings(effort="high"),
        sampling={"temperature": 0.2, "top_p": 0.5},
    )
    body = transport.last()
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": "high"}
    assert "temperature" not in body and "top_p" not in body and "tool_choice" not in body


def test_anthropic_without_reasoning_sends_no_thinking_and_keeps_sampling_for_models_that_allow_it() -> None:
    transport = _Transport(_ANTHROPIC_OK)
    _chat(
        _anthropic(transport),
        model="claude-sonnet-4-6",
        messages=[{"role": "user", "content": "q"}],
        tools=_TOOLS,
        tool_choice="required",
        sampling={"temperature": 0.2},
    )
    body = transport.last()
    assert "thinking" not in body and "output_config" not in body
    assert body["temperature"] == 0.2 and body["tool_choice"] == {"type": "any"}


def test_anthropic_budget_thinking_for_a_budget_model() -> None:
    transport = _Transport(_ANTHROPIC_OK)
    _chat(
        _anthropic(transport),
        model="claude-haiku-4-5",
        messages=[{"role": "user", "content": "q"}],
        max_tokens=20000,
        reasoning=ReasoningSettings(budget_tokens=5000),
    )
    assert transport.last()["thinking"] == {"type": "enabled", "budget_tokens": 5000}
    assert "output_config" not in transport.last()


def test_anthropic_returns_signed_blocks_as_provider_state_and_reasoning_only_when_kept() -> None:
    body = {
        "content": [*_SIGNED, {"type": "tool_use", "id": "t1", "name": "t", "input": {}}],
        "stop_reason": "tool_use",
    }
    out = _chat(
        _anthropic(_Transport(body)),
        model="claude-opus-5-5",
        messages=[{"role": "user", "content": "q"}],
        tools=_TOOLS,
        reasoning=ReasoningSettings(effort="low"),
    )
    assert out["provider_state"] == {"anthropic_thinking_blocks": _SIGNED}
    assert "reasoning" not in out  # exclude defaults to true
    kept = _chat(
        _anthropic(_Transport(body)),
        model="claude-opus-5-5",
        messages=[{"role": "user", "content": "q"}],
        tools=_TOOLS,
        reasoning=ReasoningSettings(effort="low", exclude=False),
    )
    assert kept["reasoning"] == "hm"


def test_anthropic_replays_signed_blocks_first_and_flags_tool_errors() -> None:
    transport = _Transport(_ANTHROPIC_OK)
    messages = _tool_loop({"anthropic_thinking_blocks": _SIGNED})
    messages[2]["is_error"] = True
    _chat(
        _anthropic(transport),
        model="claude-opus-5-5",
        messages=messages,
        tools=_TOOLS,
        reasoning=ReasoningSettings(effort="low"),
    )
    body = transport.last()
    assert body["thinking"] == {"type": "adaptive"}
    assert body["messages"][1]["content"][:2] == _SIGNED
    assert body["messages"][1]["content"][2]["type"] == "tool_use"
    assert body["messages"][2]["content"][0]["is_error"] is True


def test_anthropic_leaves_thinking_off_when_a_tool_turn_lost_its_blocks() -> None:
    transport = _Transport(_ANTHROPIC_OK)
    _chat(
        _anthropic(transport),
        model="claude-opus-5-5",
        messages=_tool_loop(None),
        tools=_TOOLS,
        reasoning=ReasoningSettings(effort="high"),
    )
    body = transport.last()
    assert "thinking" not in body
    assert body["output_config"] == {"effort": "high"}


def test_anthropic_does_not_replay_blocks_when_thinking_is_turned_off() -> None:
    transport = _Transport(_ANTHROPIC_OK)
    _chat(
        _anthropic(transport),
        model="claude-opus-5-5",
        messages=_tool_loop({"anthropic_thinking_blocks": _SIGNED}),
        tools=_TOOLS,
        reasoning=ReasoningSettings(effort="none"),
    )
    kinds = [b["type"] for b in transport.last()["messages"][1]["content"]]
    assert kinds == ["tool_use"]


def test_anthropic_stream_collects_signed_blocks_in_order() -> None:
    lines = [
        'data: {"type":"content_block_start","index":0,'
        '"content_block":{"type":"thinking","thinking":"","signature":""}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"a"}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"b"}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"signature_delta","signature":"S"}}',
        'data: {"type":"content_block_start","index":1,"content_block":{"type":"redacted_thinking","data":"zz"}}',
        'data: {"type":"content_block_start","index":2,"content_block":{"type":"tool_use","id":"t","name":"t"}}',
        'data: {"type":"content_block_delta","index":2,"delta":{"type":"input_json_delta","partial_json":"{}"}}',
        'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":3}}',
    ]
    out = _chat(
        _anthropic(_Transport(lines=lines), stream=True),
        model="claude-opus-5-5",
        messages=[{"role": "user", "content": "q"}],
        tools=_TOOLS,
        reasoning=ReasoningSettings(effort="low", exclude=False),
    )
    assert out["provider_state"]["anthropic_thinking_blocks"] == [
        {"type": "thinking", "thinking": "ab", "signature": "S"},
        {"type": "redacted_thinking", "data": "zz"},
    ]
    assert out["reasoning"] == "ab"


# -- bedrock ------------------------------------------------------------------------------


def test_bedrock_thinking_request_reasoning_replay_and_tool_status() -> None:
    reasoning_block = {"reasoningContent": {"reasoningText": {"text": "hm", "signature": "s"}}}
    ok = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [reasoning_block, {"toolUse": {"toolUseId": "u", "name": "t", "input": {}}}],
            }
        },
        "stopReason": "tool_use",
    }
    transport = _Transport(ok)
    out = _chat(
        _bedrock(transport),
        model="claude-opus-5-5",
        messages=[{"role": "user", "content": "q"}],
        tools=_TOOLS,
        tool_choice="required",
        reasoning=ReasoningSettings(effort="medium"),
        sampling={"temperature": 0.3, "top_p": 0.4},
        extra_body={"additionalModelRequestFields": {"anthropic_beta": ["x"]}},
    )
    body = transport.last()
    assert body["additionalModelRequestFields"] == {
        "anthropic_beta": ["x"],
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "medium"},
    }
    assert "temperature" not in body.get("inferenceConfig", {}) and "topP" not in body.get("inferenceConfig", {})
    assert "toolChoice" not in body["toolConfig"]
    assert out["provider_state"] == {"bedrock_reasoning_blocks": [reasoning_block]}

    messages = _tool_loop(out["provider_state"])
    messages[2]["is_error"] = True
    _chat(
        _bedrock(transport),
        model="claude-opus-5-5",
        messages=messages,
        tools=_TOOLS,
        reasoning=ReasoningSettings(effort="medium"),
    )
    sent = transport.last()["messages"]
    assert sent[1]["content"][0] == reasoning_block
    assert sent[2]["content"][0]["toolResult"]["status"] == "error"


def test_bedrock_sends_no_thinking_for_a_model_the_catalog_does_not_list() -> None:
    transport = _Transport({"output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}}})
    _chat(
        _bedrock(transport),
        model="amazon.nova-pro-v1:0",
        messages=[{"role": "user", "content": "q"}],
        reasoning=ReasoningSettings(effort="high"),
    )
    assert "additionalModelRequestFields" not in transport.last()


# -- gemini -------------------------------------------------------------------------------


def test_gemini_thinking_config_thoughts_and_signatures_round_trip() -> None:
    ok = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [
                        {"text": "private", "thought": True},
                        {"text": "answer"},
                        {"functionCall": {"name": "t", "args": {}}, "thoughtSignature": "sig-A"},
                        {"functionCall": {"name": "t", "args": {}}},
                    ],
                },
                "finishReason": "STOP",
            }
        ]
    }
    transport = _Transport(ok)
    out = _chat(
        _gemini(transport),
        model="gemini-3-pro-preview",
        messages=[{"role": "user", "content": "q"}],
        tools=_TOOLS,
        reasoning=ReasoningSettings(effort="high", exclude=False),
    )
    assert transport.last()["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "high", "includeThoughts": True}
    assert out["content"] == "answer" and out["reasoning"] == "private"
    assert out["provider_state"] == {"gemini_thought_signatures": ["sig-A", ""]}

    messages = [
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "t", "arguments": "{}"}},
                {"id": "b", "type": "function", "function": {"name": "t", "arguments": "{}"}},
            ],
            "provider_state": out["provider_state"],
        },
        {"role": "tool", "tool_call_id": "a", "content": "r", "is_error": True},
    ]
    _chat(_gemini(transport), model="gemini-3-pro-preview", messages=messages, tools=_TOOLS)
    contents = transport.last()["contents"]
    calls = contents[1]["parts"]
    assert calls[0]["thoughtSignature"] == "sig-A"
    assert calls[1]["thoughtSignature"] == "skip_thought_signature_validator"
    assert contents[2]["parts"][0]["functionResponse"]["response"] == {"error": "r"}


def test_gemini_stream_collects_signatures_and_hides_thoughts() -> None:
    lines = [
        'data: {"candidates":[{"content":{"parts":[{"text":"t","thought":true}]}}]}',
        'data: {"candidates":[{"content":{"parts":[{"text":"hi"}]}}]}',
        'data: {"candidates":[{"content":{"parts":[{"functionCall":{"name":"t","args":{}},"thoughtSignature":"S"}]},'
        '"finishReason":"STOP"}]}',
    ]
    seen: list[str] = []
    out = _chat(
        _gemini(_Transport(lines=lines), stream=True),
        model="gemini-2.5-pro",
        messages=[{"role": "user", "content": "q"}],
        tools=_TOOLS,
        on_stream_chunk=seen.append,
    )
    assert out["content"] == "hi" and seen == ["hi"]
    assert out["provider_state"] == {"gemini_thought_signatures": ["S"]}
