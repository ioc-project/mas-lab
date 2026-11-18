#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Anthropic Messages protocol layer over a fake http transport."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from mas.library.standard.plugins.llm.anthropic import AnthropicLLMProvider
from mas.runtime.engine.infra_pipeline import _PROVIDER_CALL
from mas.runtime.reliability.classes import ClassifiedFailure, FailureClass

_OK = {
    "content": [{"type": "text", "text": "ok"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 4, "output_tokens": 2},
}


class _Transport:
    def __init__(
        self, body: dict[str, Any] | None = None, *, status: int = 200, lines: list[str] | None = None
    ) -> None:
        self.requests: list[httpx.Request] = []
        self.body = body if body is not None else _OK
        self.status = status
        self.lines = lines
        self.discarded = 0
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

    def discard_client(self) -> None:
        self.discarded += 1

    async def adiscard_aclient(self) -> None:
        self.discarded += 1


def _provider(transport: _Transport, monkeypatch: pytest.MonkeyPatch, **params: Any) -> AnthropicLLMProvider:
    del monkeypatch
    return AnthropicLLMProvider.wrap(
        transport,
        {"api_base": "http://127.0.0.1:9/v1", "api_key": "secret", "api_key_env": "K", **params},
        call_kinds=("llm",),
    )


def _chat(provider, **kwargs):
    return asyncio.run(provider.achat_completion(**kwargs))


def test_a_chat_maps_messages_tools_and_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _Transport(
        {
            "content": [
                {"type": "text", "text": "hi"},
                {"type": "tool_use", "id": "t1", "name": "echo", "input": {"q": 1}},
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 9, "output_tokens": 3},
        }
    )
    provider = _provider(transport, monkeypatch)
    out = _chat(
        provider,
        model="claude-sonnet",
        messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "echo", "arguments": '{"q":1}'}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "pong"},
        ],
        tools=[
            {"type": "function", "function": {"name": "echo", "description": "e", "parameters": {"type": "object"}}}
        ],
        tool_choice="required",
        max_tokens=16,
    )
    req = transport.requests[0]
    assert req.url.path.endswith("/messages")
    assert req.headers["x-api-key"] == "secret"
    assert req.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(req.content)
    assert body["system"][0]["text"] == "sys"
    assert body["max_tokens"] == 16
    assert body["tool_choice"] == {"type": "any"}
    assert body["tools"][0]["name"] == "echo"
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][1]["content"][0]["type"] == "tool_use"
    assert body["messages"][2]["content"][0]["type"] == "tool_result"
    assert out["content"] == "hi"
    assert out["tool_calls"][0]["function"]["name"] == "echo"
    assert out["finish_reason"] == "tool_calls"
    assert out["usage"]["prompt_tokens"] == 9
    assert out["usage"]["total_tokens"] == 12


def test_protocol_layers_is_http_then_anthropic() -> None:
    steps = AnthropicLLMProvider.protocol_layers({"api_base": "http://127.0.0.1:9/v1", "timeout": 7})
    assert [step["layer"] for step in steps] == ["http", "anthropic"]
    assert steps[0]["params"] == {"url": "http://127.0.0.1:9/v1", "timeout": 7.0}


def test_missing_key_is_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("K", "from-env")
    provider = AnthropicLLMProvider.wrap(_Transport(), {"api_base": "http://127.0.0.1:9/v1", "api_key_env": "K"})
    with pytest.raises(RuntimeError, match="Missing API key"):
        _chat(provider, model="m", messages=[{"role": "user", "content": "q"}])


def test_http_401_is_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(_Transport(status=401), monkeypatch)
    with pytest.raises(ClassifiedFailure) as exc:
        _chat(provider, model="m", messages=[{"role": "user", "content": "q"}])
    assert exc.value.failure_class is FailureClass.FATAL
    assert "401" in (exc.value.code or "")


def test_stream_text_and_tool_json(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [
        'data: {"type":"message_start","message":{"usage":{"input_tokens":2}}}',
        'data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"t1","name":"echo"}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hel"}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"lo"}}',
        'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"q\\":"}}',
        'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"1}"}}',
        'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":4}}',
        "data: [DONE]",
        "event: ping",
        "not-json",
    ]
    received: list[str] = []
    provider = _provider(_Transport(lines=lines), monkeypatch, stream=True)
    out = _chat(provider, model="m", messages=[{"role": "user", "content": "q"}], on_stream_chunk=received.append)
    assert "".join(received) == "Hello"
    assert out["content"] == "Hello"
    assert out["tool_calls"][0]["function"]["arguments"] == '{"q":1}'
    assert out["finish_reason"] == "tool_calls"
    assert out["usage"]["prompt_tokens"] == 2


def test_async_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(_Transport(), monkeypatch)
    out = asyncio.run(provider.achat_completion(model="m", messages=[{"role": "user", "content": "q"}]))
    assert out["content"] == "ok"


def test_async_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"z"}}',
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}',
    ]
    provider = _provider(_Transport(lines=lines), monkeypatch, stream=True)
    out = asyncio.run(provider.achat_completion(model="m", messages=[{"role": "user", "content": "q"}]))
    assert out["content"] == "z"
    assert out["finish_reason"] == "stop"


def test_output_format_from_response_format(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _Transport({"content": [{"type": "text", "text": '{"n": 1}'}], "stop_reason": "end_turn"})
    provider = _provider(transport, monkeypatch)
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    out = _chat(
        provider,
        model="m",
        messages=[{"role": "user", "content": "q"}],
        response_format={"type": "json_schema", "json_schema": {"name": "r", "schema": schema}},
    )
    body = json.loads(transport.requests[0].content)
    assert body["output_config"] == {"format": {"type": "json_schema", "schema": schema}}
    assert "output_format" not in body
    assert "response_format" not in body
    assert out["parsed"] == {"n": 1}


def test_invoke_uses_provider_result_return(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(_Transport(), monkeypatch)
    payload = {"model": "m", "messages": [{"role": "user", "content": "q"}]}
    token = _PROVIDER_CALL.set(payload)
    try:
        ret = asyncio.run(provider.ainvoke(SimpleNamespace(correlation_id=1)))
        assert payload["_result"]["content"] == "ok"
        assert '"content": "ok"' in ret.text
    finally:
        _PROVIDER_CALL.reset(token)


def test_tool_choice_none_and_named(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _Transport()
    provider = _provider(transport, monkeypatch)
    _chat(
        provider,
        model="m",
        messages=[{"role": "user", "content": "q"}],
        tools=[{"type": "function", "function": {"name": "echo", "parameters": {}}}],
        tool_choice={"type": "function", "function": {"name": "echo"}},
    )
    body = json.loads(transport.requests[0].content)
    assert body["tool_choice"] == {"type": "tool", "name": "echo"}
    transport.requests.clear()
    _chat(
        provider,
        model="m",
        messages=[{"role": "user", "content": "q"}],
        tools=[{"type": "function", "function": {"name": "echo"}}],
        tool_choice="none",
    )
    assert json.loads(transport.requests[0].content)["tool_choice"] == {"type": "none"}


def test_timeout_is_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _Transport()
    transport.aclient = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _r: (_ for _ in ()).throw(httpx.TimeoutException("slow")))
    )
    provider = _provider(transport, monkeypatch)
    with pytest.raises(ClassifiedFailure) as exc:
        _chat(provider, model="m", messages=[{"role": "user", "content": "q"}])
    assert exc.value.failure_class is FailureClass.TRANSIENT


def test_connect_error_drops_the_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _Transport()
    transport.aclient = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _r: (_ for _ in ()).throw(httpx.ConnectError("down")))
    )
    provider = _provider(transport, monkeypatch)
    with pytest.raises(ClassifiedFailure):
        _chat(provider, model="m", messages=[{"role": "user", "content": "q"}])
    assert transport.discarded == 1


def test_request_extras_sampling_and_list_content(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _Transport()
    provider = _provider(transport, monkeypatch, extra={"keep": True})
    _chat(
        provider,
        model="m",
        messages=[{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
        sampling={"top_p": 0.2, "top_k": 4, "stop": "END"},
        extra_headers={"X-Test": "1"},
        extra_query={"a": "b"},
        extra_body={"metadata": {"k": 1}},
        tools=[{"type": "function", "function": {"name": ""}}, {"function": {"name": "ok", "parameters": {}}}],
        max_completion_tokens=8,
    )
    req = transport.requests[0]
    assert req.headers["X-Test"] == "1"
    assert req.url.params.get("a") == "b"
    body = json.loads(req.content)
    assert body["messages"][0]["content"][0]["text"] == "hello"
    assert body["top_p"] == 0.2
    assert body["stop_sequences"] == ["END"]
    assert body["keep"] is True
    assert body["metadata"] == {"k": 1}
    assert body["max_tokens"] == 8
    assert [t["name"] for t in body["tools"]] == ["ok"]


def test_malformed_tool_arguments_become_empty_input(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _Transport()
    provider = _provider(transport, monkeypatch)
    _chat(
        provider,
        model="m",
        messages=[
            {
                "role": "assistant",
                "tool_calls": [{"id": "c1", "function": {"name": "echo", "arguments": "not-json"}}],
            }
        ],
    )
    block = json.loads(transport.requests[0].content)["messages"][0]["content"][0]
    assert block["input"] == {}


def test_empty_completion_and_connect_stream_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(_Transport({"content": [], "stop_reason": ""}), monkeypatch)
    out = _chat(provider, model="m", messages=[{"role": "user", "content": "q"}])
    assert not out.get("content")
    streamed = _provider(_Transport(), monkeypatch, stream=True)
    streamed.inner.aclient = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _r: (_ for _ in ()).throw(httpx.ConnectError("down")))
    )
    with pytest.raises(ClassifiedFailure) as exc:
        _chat(streamed, model="m", messages=[{"role": "user", "content": "q"}])
    assert exc.value.code != "STREAM_PARTIAL"


def test_non_dict_blocks_json_args_and_stream_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _Transport({"content": ["skip", {"type": "text", "text": "ok"}], "stop_reason": "end_turn"})
    provider = _provider(transport, monkeypatch)
    out = _chat(
        provider,
        model="m",
        messages=[
            {
                "role": "assistant",
                "content": "said",
                "tool_calls": [{"id": "c1", "function": {"name": "echo", "arguments": "[1]"}}],
            }
        ],
        tool_choice={"type": "function"},
    )
    assert out["content"] == "ok"
    body = json.loads(transport.requests[0].content)
    assert "tool_choice" not in body
    assert body["messages"][0]["content"][1]["input"] == {"value": [1]}

    lines = [
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"x"}}',
        "data: {",
        "data: []",
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":""}}',
    ]
    streamed = _provider(_Transport(lines=lines), monkeypatch, stream=True)

    def boom(_text: str) -> None:
        raise RuntimeError("cb")

    assert (
        _chat(streamed, model="m", messages=[{"role": "user", "content": "q"}], on_stream_chunk=boom)["content"] == "x"
    )
    asyncio.run(
        _provider(_Transport(lines=lines), monkeypatch, stream=True).achat_completion(
            model="m", messages=[{"role": "user", "content": "q"}]
        )
    )


def test_async_stream_connect_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    streamed = _provider(_Transport(), monkeypatch, stream=True)
    streamed.inner.aclient = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _r: (_ for _ in ()).throw(httpx.ConnectError("down")))
    )
    with pytest.raises(ClassifiedFailure):
        asyncio.run(streamed.achat_completion(model="m", messages=[{"role": "user", "content": "q"}]))


def test_no_temperature_and_empty_turns_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _Transport()
    _chat(
        _provider(transport, monkeypatch),
        model="m",
        temperature=0.7,
        messages=[
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": ""},
            {"role": "user", "content": "again"},
        ],
    )
    body = json.loads(transport.requests[0].content)
    assert "temperature" not in body
    assert [m["content"][0]["text"] for m in body["messages"]] == ["q", "again"]


def test_stream_error_event_is_a_classified_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"par"}}',
        'data: {"type":"error","error":{"type":"overloaded_error","message":"busy"}}',
    ]
    provider = _provider(_Transport(lines=lines), monkeypatch, stream=True)
    with pytest.raises(ClassifiedFailure) as exc:
        _chat(provider, model="m", messages=[{"role": "user", "content": "q"}])
    assert exc.value.failure_class is FailureClass.TRANSIENT
    assert exc.value.code == "overloaded_error"


def test_stream_usage_total_is_input_plus_output(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [
        'data: {"type":"message_start","message":{"usage":{"input_tokens":10,"output_tokens":1}}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"x"}}',
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":5}}',
    ]
    provider = _provider(_Transport(lines=lines), monkeypatch, stream=True)
    out = _chat(provider, model="m", messages=[{"role": "user", "content": "q"}])
    assert out["usage"]["total_tokens"] == 15


def test_prompt_cache_marks_system_last_tool_and_last_two_users(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _Transport()
    tool = {"type": "function", "function": {"name": "a", "parameters": {"type": "object"}}}
    _chat(
        _provider(transport, monkeypatch),
        model="m",
        tools=[tool, {"type": "function", "function": {"name": "b"}}, tool],
        messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "two"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "three"},
        ],
    )
    body = json.loads(transport.requests[0].content)
    assert [t["name"] for t in body["tools"]] == ["a", "b"]
    assert "cache_control" in body["tools"][-1] and "cache_control" not in body["tools"][0]
    assert "cache_control" in body["system"][0]
    marked = [m["role"] == "user" and "cache_control" in m["content"][-1] for m in body["messages"]]
    assert marked == [False, False, True, False, True]


def test_prompt_cache_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _Transport()
    provider = _provider(transport, monkeypatch)
    _chat(
        provider,
        model="m",
        sampling={"prompt_cache_breakpoints": False},
        messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "q"}],
    )
    body = json.loads(transport.requests[0].content)
    assert body["system"] == "sys" and body["messages"][0]["content"] == "q"


def test_cache_tokens_count_as_prompt_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {
        "content": [{"type": "text", "text": "ok"}],
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": 3,
            "output_tokens": 2,
            "cache_creation_input_tokens": 10,
            "cache_read_input_tokens": 100,
        },
    }
    out = _chat(_provider(_Transport(body), monkeypatch), model="m", messages=[{"role": "user", "content": "q"}])
    assert out["usage"]["prompt_tokens"] == 113
    assert out["usage"]["total_tokens"] == 115


def test_refusal_is_a_failure_with_its_category(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {
        "content": [],
        "stop_reason": "refusal",
        "stop_details": {"type": "refusal", "category": "cyber", "explanation": "not today"},
    }
    provider = _provider(_Transport(body), monkeypatch)
    with pytest.raises(ClassifiedFailure) as exc:
        _chat(provider, model="m", messages=[{"role": "user", "content": "q"}])
    assert exc.value.code == "REFUSAL"
    assert "cyber" in str(exc.value) and "not today" in str(exc.value)


def test_tool_call_cut_off_by_max_tokens_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [
        'data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"t","name":"w"}}',
        'data: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"input_json_delta","partial_json":"{\\"a\\": "}}',
        'data: {"type":"message_delta","delta":{"stop_reason":"max_tokens"},"usage":{"output_tokens":9}}',
    ]
    provider = _provider(_Transport(lines=lines), monkeypatch, stream=True)
    with pytest.raises(ClassifiedFailure) as exc:
        _chat(provider, model="m", messages=[{"role": "user", "content": "q"}])
    assert exc.value.code == "TRUNCATED_TOOL_CALL"
