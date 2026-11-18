#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Open Responses protocol layer over a fake http transport."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from mas.library.standard.plugins.llm.openresponses import OpenResponsesLLMProvider
from mas.runtime.engine.llm_reasoning import ReasoningSettings
from mas.runtime.reliability.classes import ClassifiedFailure, FailureClass

_OK = {
    "status": "completed",
    "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "ok"}]}],
    "usage": {
        "input_tokens": 10,
        "output_tokens": 4,
        "total_tokens": 14,
        "input_tokens_details": {"cached_tokens": 6},
        "output_tokens_details": {"reasoning_tokens": 3},
    },
}


class _Transport:
    def __init__(
        self, body: dict[str, Any] | None = None, *, status: int = 200, lines: list[str] | None = None
    ) -> None:
        self.requests: list[httpx.Request] = []
        self.body = body if body is not None else _OK
        self.status = status
        self.lines = lines
        handler = httpx.MockTransport(self._answer)
        self.client = httpx.Client(transport=handler)
        self.aclient = httpx.AsyncClient(transport=handler)

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status >= 400:
            return httpx.Response(self.status, json={"error": {"message": "nope"}}, request=request)
        if self.lines is not None:
            return httpx.Response(200, content=("\n".join(self.lines) + "\n").encode())
        return httpx.Response(200, json=self.body)

    def sent(self) -> dict[str, Any]:
        return json.loads(self.requests[-1].content)


def _provider(transport: _Transport, **params: Any) -> OpenResponsesLLMProvider:
    return OpenResponsesLLMProvider.wrap(
        transport,
        {"api_base": "http://127.0.0.1:9/v1", "api_key": "secret", "api_key_env": "K", **params},
        call_kinds=("llm",),
    )


def _chat(provider: OpenResponsesLLMProvider, **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("model", "m")
    return asyncio.run(provider.achat_completion(**kwargs))


def _sse(*events: dict[str, Any]) -> list[str]:
    return [f"data: {json.dumps(e)}" for e in events]


def test_a_chat_posts_responses_with_typed_items_and_maps_usage() -> None:
    transport = _Transport()
    message = _chat(
        _provider(transport),
        messages=[
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "calling",
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "echo", "arguments": '{"q":1}'}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "out"},
        ],
        tools=[
            {"type": "function", "function": {"name": "echo", "description": "d", "parameters": {"type": "object"}}}
        ],
        max_tokens=50,
    )
    request = transport.requests[0]
    assert str(request.url) == "http://127.0.0.1:9/v1/responses"
    assert request.headers["authorization"] == "Bearer secret"
    body = transport.sent()
    assert body["instructions"] == "be brief"
    assert body["store"] is False
    assert body["max_output_tokens"] == 50
    assert [i["type"] for i in body["input"]] == ["message", "message", "function_call", "function_call_output"]
    assert body["input"][0]["content"] == [{"type": "input_text", "text": "hi"}]
    assert body["input"][1]["content"] == [{"type": "output_text", "text": "calling"}]
    assert body["input"][2] == {"type": "function_call", "call_id": "c1", "name": "echo", "arguments": '{"q":1}'}
    assert body["input"][3] == {"type": "function_call_output", "call_id": "c1", "output": "out"}
    assert body["tools"] == [{"type": "function", "name": "echo", "description": "d", "parameters": {"type": "object"}}]
    assert message["content"] == "ok"
    assert message["finish_reason"] == "stop"
    usage = message["usage"]
    assert (usage["prompt_tokens"], usage["completion_tokens"], usage["total_tokens"]) == (10, 4, 14)
    assert (usage["cached_tokens"], usage["reasoning_tokens"]) == (6, 3)


def test_function_call_output_becomes_tool_calls_and_blank_results_get_a_placeholder() -> None:
    transport = _Transport(
        {
            "status": "completed",
            "output": [{"type": "function_call", "call_id": "c9", "name": "echo", "arguments": '{"a":2}'}],
        }
    )
    message = _chat(
        _provider(transport),
        messages=[{"role": "user", "content": "go"}, {"role": "tool", "tool_call_id": "c0", "content": "  "}],
    )
    assert message["tool_calls"] == [
        {"id": "c9", "type": "function", "function": {"name": "echo", "arguments": '{"a":2}'}}
    ]
    assert message["finish_reason"] == "tool_calls"
    assert transport.sent()["input"][-1]["output"] == "(no output)"


def test_tool_choice_and_duplicate_tool_names() -> None:
    transport = _Transport()
    tool = {"type": "function", "function": {"name": "echo", "parameters": {"type": "object"}}}
    _chat(
        _provider(transport),
        messages=[{"role": "user", "content": "x"}],
        tools=[tool, tool],
        tool_choice={"type": "function", "function": {"name": "echo"}},
    )
    body = transport.sent()
    assert len(body["tools"]) == 1
    assert body["tool_choice"] == {"type": "function", "name": "echo"}
    _chat(_provider(transport), messages=[{"role": "user", "content": "x"}], tool_choice="required")
    assert "tool_choice" not in transport.sent()


def test_reasoning_is_sent_and_encrypted_items_round_trip_with_the_tool_call() -> None:
    reasoning = ReasoningSettings(effort="high", exclude=False)
    item = {
        "type": "reasoning",
        "id": "rs_1",
        "summary": [{"type": "summary_text", "text": "plan"}],
        "encrypted_content": "enc",
    }
    transport = _Transport(
        {
            "status": "completed",
            "output": [item, {"type": "function_call", "call_id": "c1", "name": "echo", "arguments": "{}"}],
        }
    )
    provider = _provider(transport)
    message = _chat(provider, messages=[{"role": "user", "content": "go"}], reasoning=reasoning)
    body = transport.sent()
    assert body["reasoning"] == {"effort": "high", "summary": "auto"}
    assert body["include"] == ["reasoning.encrypted_content"]
    assert message["reasoning"] == "plan"
    assert message["provider_state"] == {"openresponses_reasoning_items": [item]}

    _chat(
        provider,
        messages=[
            {"role": "user", "content": "go"},
            {**message, "role": "assistant"},
            {"role": "tool", "tool_call_id": "c1", "content": "r"},
        ],
        reasoning=reasoning,
    )
    types = [i["type"] for i in transport.sent()["input"]]
    assert types == ["message", "reasoning", "function_call", "function_call_output"]
    assert transport.sent()["input"][1]["encrypted_content"] == "enc"


def test_thinking_off_sends_no_encrypted_include_and_no_replay() -> None:
    transport = _Transport()
    state = {
        "openresponses_reasoning_items": [{"type": "reasoning", "id": "r", "summary": [], "encrypted_content": "e"}]
    }
    _chat(
        _provider(transport),
        messages=[
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": None,
                "provider_state": state,
                "tool_calls": [{"id": "c", "type": "function", "function": {"name": "t", "arguments": "{}"}}],
            },
        ],
        reasoning=ReasoningSettings(effort="none"),
    )
    body = transport.sent()
    assert "include" not in body
    assert body["reasoning"] == {"effort": "none"}
    assert [i["type"] for i in body["input"]] == ["message", "function_call"]


def test_prompt_cache_fields_follow_open_responses_names() -> None:
    transport = _Transport()
    _chat(
        _provider(transport),
        messages=[{"role": "user", "content": "x"}],
        sampling={
            "prompt_cache_key": "k1",
            "prompt_cache_retention": "1h",
            "prompt_cache_breakpoints": {"system": True},
        },
    )
    body = transport.sent()
    assert body["prompt_cache_key"] == "k1"
    assert body["prompt_cache_retention"] == "24h"
    assert "prompt_cache_breakpoints" not in body
    _chat(_provider(transport), messages=[{"role": "user", "content": "x"}], sampling={"prompt_cache_retention": "5m"})
    assert transport.sent()["prompt_cache_retention"] == "in_memory"


def test_sampling_goes_through_the_catalog_gate() -> None:
    transport = _Transport()
    _chat(
        _provider(transport), messages=[{"role": "user", "content": "x"}], sampling={"temperature": 0.2, "top_p": 0.9}
    )
    body = transport.sent()
    assert (body["temperature"], body["top_p"]) == (0.2, 0.9)
    _chat(_provider(transport), messages=[{"role": "user", "content": "x"}])
    assert "temperature" not in transport.sent()


def test_structured_output_maps_to_text_format_and_validates() -> None:
    transport = _Transport(
        {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"a": 1}'}]}],
        }
    )
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}
    message = _chat(
        _provider(transport),
        messages=[{"role": "user", "content": "x"}],
        response_format={"type": "json_schema", "json_schema": {"name": "r", "schema": schema}},
    )
    fmt = transport.sent()["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"] == schema and fmt["strict"] is True
    assert message["parsed"] == {"a": 1}


def test_incomplete_maps_to_length_and_a_cut_tool_call_fails() -> None:
    length = _Transport(
        {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "par"}]}],
        }
    )
    assert _chat(_provider(length), messages=[{"role": "user", "content": "x"}])["finish_reason"] == "length"
    cut = _Transport(
        {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [{"type": "function_call", "call_id": "c", "name": "t", "arguments": '{"a":'}],
        }
    )
    with pytest.raises(ClassifiedFailure) as err:
        _chat(_provider(cut), messages=[{"role": "user", "content": "x"}])
    assert err.value.code == "TRUNCATED_TOOL_CALL"
    filtered = _Transport({"status": "incomplete", "incomplete_details": {"reason": "content_filter"}, "output": []})
    assert _chat(_provider(filtered), messages=[{"role": "user", "content": "x"}])["finish_reason"] == "content_filter"


def test_failed_response_and_refusal_are_failures() -> None:
    failed = _Transport({"status": "failed", "error": {"code": "server_error", "message": "boom"}})
    with pytest.raises(ClassifiedFailure) as err:
        _chat(_provider(failed), messages=[{"role": "user", "content": "x"}])
    assert err.value.failure_class is FailureClass.TRANSIENT and err.value.code == "server_error"
    refused = _Transport(
        {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]}
    )
    with pytest.raises(ClassifiedFailure) as err:
        _chat(_provider(refused), messages=[{"role": "user", "content": "x"}])
    assert err.value.code == "REFUSAL"


def test_http_error_is_classified() -> None:
    with pytest.raises(ClassifiedFailure) as err:
        _chat(_provider(_Transport(status=429)), messages=[{"role": "user", "content": "x"}])
    assert err.value.failure_class is FailureClass.TRANSIENT


def test_stream_collects_text_calls_and_usage_from_the_terminal_event() -> None:
    final = {
        "status": "completed",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "hello"}]},
            {"type": "function_call", "call_id": "c1", "name": "echo", "arguments": "{}"},
        ],
        "usage": {"input_tokens": 3, "output_tokens": 2},
    }
    transport = _Transport(
        lines=_sse(
            {"type": "response.output_text.delta", "delta": "hel"},
            {"type": "response.output_text.delta", "delta": "lo"},
            {"type": "response.completed", "response": final},
        )
    )
    chunks: list[str] = []
    message = _chat(
        _provider(transport), messages=[{"role": "user", "content": "x"}], stream=True, on_stream_chunk=chunks.append
    )
    assert transport.sent()["stream"] is True
    assert chunks == ["hel", "lo"]
    assert message["content"] == "hello"
    assert message["tool_calls"][0]["id"] == "c1"
    assert message["usage"]["prompt_tokens"] == 3


def test_stream_without_a_terminal_event_uses_the_finished_items() -> None:
    transport = _Transport(
        lines=_sse(
            {
                "type": "response.output_item.done",
                "item": {"type": "message", "content": [{"type": "output_text", "text": "done"}]},
            }
        )
    )
    message = _chat(_provider(transport), messages=[{"role": "user", "content": "x"}], stream=True)
    assert message["content"] == "done"


def test_stream_error_event_and_failed_event_raise() -> None:
    with pytest.raises(ClassifiedFailure) as err:
        _chat(
            _provider(_Transport(lines=_sse({"type": "error", "code": "rate_limit_exceeded", "message": "slow"}))),
            messages=[{"role": "user", "content": "x"}],
            stream=True,
        )
    assert err.value.failure_class is FailureClass.TRANSIENT
    with pytest.raises(ClassifiedFailure):
        _chat(
            _provider(
                _Transport(
                    lines=_sse({"type": "response.failed", "response": {"error": {"code": "bad", "message": "m"}}})
                )
            ),
            messages=[{"role": "user", "content": "x"}],
            stream=True,
        )


def test_streamed_http_error_body_is_classified() -> None:
    transport = _Transport(status=400, lines=[])
    with pytest.raises(ClassifiedFailure):
        _chat(_provider(transport), messages=[{"role": "user", "content": "x"}], stream=True)


def test_missing_key_names_the_env_var() -> None:
    provider = OpenResponsesLLMProvider.wrap(
        _Transport(), {"api_base": "http://127.0.0.1:9/v1", "api_key_env": "MY_KEY"}, call_kinds=("llm",)
    )
    with pytest.raises(Exception, match="MY_KEY"):
        _chat(provider, messages=[{"role": "user", "content": "x"}])


def test_protocol_sugar_expands_to_http_then_openresponses() -> None:
    steps = OpenResponsesLLMProvider.protocol_layers({"api_base": "http://h/v1"})
    assert [s["layer"] for s in steps] == ["http", "openresponses"]
