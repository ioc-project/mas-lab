#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Gemini generateContent protocol layer over a fake http transport."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from mas.library.standard.plugins.llm.gemini import GeminiLLMProvider
from mas.runtime.engine.infra_pipeline import _PROVIDER_CALL
from mas.runtime.reliability.classes import ClassifiedFailure, FailureClass

_OK = {
    "candidates": [{"content": {"role": "model", "parts": [{"text": "ok"}]}, "finishReason": "STOP"}],
    "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 2, "totalTokenCount": 6},
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


def _provider(transport: _Transport, monkeypatch: pytest.MonkeyPatch, **params: Any) -> GeminiLLMProvider:
    del monkeypatch
    return GeminiLLMProvider.wrap(
        transport,
        {"api_base": "http://127.0.0.1:9/v1beta", "api_key": "secret", "api_key_env": "K", **params},
        call_kinds=("llm",),
    )


def _chat(provider, **kwargs):
    return asyncio.run(provider.achat_completion(**kwargs))


def test_a_chat_maps_contents_tools_and_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _Transport(
        {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {"text": "hi"},
                            {"functionCall": {"name": "echo", "args": {"q": 1}}},
                        ],
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3, "totalTokenCount": 8},
        }
    )
    provider = _provider(transport, monkeypatch)
    out = _chat(
        provider,
        model="models/gemini-2.5-flash",
        messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": "c",
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "echo", "arguments": '{"q":1}'}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true}'},
        ],
        tools=[
            {"type": "function", "function": {"name": "echo", "description": "e", "parameters": {"type": "object"}}}
        ],
        tool_choice="required",
        max_tokens=32,
    )
    req = transport.requests[0]
    assert req.url.path.endswith("/models/gemini-2.5-flash:generateContent")
    assert req.headers["x-goog-api-key"] == "secret"
    body = json.loads(req.content)
    assert body["systemInstruction"]["parts"][0]["text"] == "sys"
    assert body["generationConfig"]["maxOutputTokens"] == 32
    assert body["tools"][0]["functionDeclarations"][0]["name"] == "echo"
    assert body["toolConfig"]["functionCallingConfig"]["mode"] == "ANY"
    assert body["contents"][0]["role"] == "user"
    assert body["contents"][1]["role"] == "model"
    assert body["contents"][2]["parts"][0]["functionResponse"]["name"] == "echo"
    assert out["content"] == "hi"
    assert out["tool_calls"][0]["id"] == "gemini_call_0"
    assert out["tool_calls"][0]["function"]["name"] == "echo"
    assert out["finish_reason"] == "stop"
    assert out["usage"]["total_tokens"] == 8


def test_protocol_layers_is_http_then_gemini() -> None:
    steps = GeminiLLMProvider.protocol_layers({"api_base": "http://127.0.0.1:9/v1beta", "verify_ssl": False})
    assert [step["layer"] for step in steps] == ["http", "gemini"]
    assert steps[0]["params"]["verify_ssl"] is False
    assert steps[0]["params"]["timeout"] == 120.0


def test_http_429_is_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(_Transport(status=429), monkeypatch)
    with pytest.raises(ClassifiedFailure) as exc:
        _chat(provider, model="m", messages=[{"role": "user", "content": "q"}])
    assert exc.value.failure_class is FailureClass.TRANSIENT


def test_stream_text_and_function_call(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [
        'data: {"candidates":[{"content":{"parts":[{"text":"Hel"}]}}]}',
        '{"candidates":[{"content":{"parts":[{"text":"lo"},{"functionCall":{"name":"echo","args":{"q":1}}}]},"finishReason":"STOP"}],"usageMetadata":{"totalTokenCount":3}}',
        "data: [DONE]",
        "{",
    ]
    received: list[str] = []
    provider = _provider(_Transport(lines=lines), monkeypatch, stream=True)
    out = _chat(provider, model="m", messages=[{"role": "user", "content": "q"}], on_stream_chunk=received.append)
    req = provider.inner.requests[0]
    assert req.url.path.endswith(":streamGenerateContent")
    assert req.url.params.get("alt") == "sse"
    assert "".join(received) == "Hello"
    assert out["content"] == "Hello"
    assert out["tool_calls"][0]["id"] == "gemini_call_0"
    assert out["tool_calls"][0]["function"]["name"] == "echo"
    assert json.loads(req.content).get("response_format") is None


def test_async_chat_and_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(_Transport(), monkeypatch)
    out = asyncio.run(provider.achat_completion(model="m", messages=[{"role": "user", "content": "q"}]))
    assert out["content"] == "ok"
    lines = ['data: {"candidates":[{"content":{"parts":[{"text":"z"}]},"finishReason":"MAX_TOKENS"}]}']
    streamed = _provider(_Transport(lines=lines), monkeypatch, stream=True)
    out = asyncio.run(streamed.achat_completion(model="m", messages=[{"role": "user", "content": "q"}]))
    assert out["content"] == "z"
    assert out["finish_reason"] == "length"


def test_response_schema_on_generation_config(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _Transport({"candidates": [{"content": {"parts": [{"text": '{"n": 2}'}]}, "finishReason": "STOP"}]})
    provider = _provider(transport, monkeypatch)
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    out = _chat(
        provider,
        model="m",
        messages=[{"role": "user", "content": "q"}],
        response_format={"schema": schema},
    )
    body = json.loads(transport.requests[0].content)
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["responseJsonSchema"] == schema
    assert "response_format" not in body
    assert out["parsed"] == {"n": 2}


def test_response_schema_survives_extra_generation_config(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _Transport({"candidates": [{"content": {"parts": [{"text": '{"n": 2}'}]}, "finishReason": "STOP"}]})
    provider = _provider(transport, monkeypatch)
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    out = _chat(
        provider,
        model="m",
        messages=[{"role": "user", "content": "q"}],
        response_format={"schema": schema},
        extra_body={"generationConfig": {"temperature": 0.1}},
    )
    body = json.loads(transport.requests[0].content)
    assert body["generationConfig"]["temperature"] == 0.1
    assert body["generationConfig"]["responseJsonSchema"] == schema
    assert out["parsed"] == {"n": 2}
    transport.requests.clear()
    _chat(
        provider,
        model="m",
        messages=[{"role": "user", "content": "q"}],
        response_format={"schema": schema},
        extra_body={"generationConfig": "nope"},
    )
    assert json.loads(transport.requests[0].content)["generationConfig"]["responseJsonSchema"] == schema


def test_invoke_needs_a_chat_in_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(_Transport(), monkeypatch)
    with pytest.raises(RuntimeError, match="in flight"):
        asyncio.run(provider.ainvoke(SimpleNamespace(correlation_id=1)))
    payload = {"model": "m", "messages": [{"role": "user", "content": "q"}]}
    token = _PROVIDER_CALL.set(payload)
    try:
        assert asyncio.run(provider.ainvoke(SimpleNamespace(correlation_id=2))).text
    finally:
        _PROVIDER_CALL.reset(token)


def test_tool_choice_none(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _Transport()
    provider = _provider(transport, monkeypatch)
    _chat(
        provider,
        model="m",
        messages=[{"role": "user", "content": "q"}],
        tools=[{"type": "function", "function": {"name": "echo"}}],
        tool_choice="none",
    )
    assert json.loads(transport.requests[0].content)["toolConfig"]["functionCallingConfig"]["mode"] == "NONE"


def test_named_tool_choice_and_non_json_tool_result(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _Transport()
    provider = _provider(transport, monkeypatch)
    _chat(
        provider,
        model="gemini-2.5-flash",
        messages=[
            {"role": "user", "content": [{"text": "q"}]},
            {
                "role": "assistant",
                "tool_calls": [{"id": "c1", "function": {"name": "echo", "arguments": "not-json"}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "plain"},
        ],
        tools=[{"name": "echo"}, {"name": ""}],
        tool_choice={"function": {"name": "echo"}},
        extra_headers={"X-Test": "1"},
        sampling={"top_p": 0.5, "stop": ["END"]},
        max_completion_tokens=9,
    )
    req = transport.requests[0]
    assert req.headers["X-Test"] == "1"
    assert req.url.path.endswith("/models/gemini-2.5-flash:generateContent")
    body = json.loads(req.content)
    assert body["toolConfig"]["functionCallingConfig"]["allowedFunctionNames"] == ["echo"]
    assert body["generationConfig"]["maxOutputTokens"] == 9
    assert body["contents"][-1]["parts"][0]["functionResponse"]["response"] == {"result": "plain"}


def test_empty_assistant_merges_users_and_list_args(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _Transport()
    provider = _provider(transport, monkeypatch)
    _chat(
        provider,
        model="m",
        messages=[
            {"role": "user", "content": "a"},
            {"role": "user", "content": "b"},
            {"role": "assistant", "content": None},
            {
                "role": "assistant",
                "tool_calls": [{"id": "c1", "function": {"name": "echo", "arguments": "[1]"}}],
            },
        ],
        extra_query={"x": "1"},
    )
    body = json.loads(transport.requests[0].content)
    assert body["contents"][0]["parts"][0]["text"] == "a"
    assert body["contents"][0]["parts"][1]["text"] == "b"
    assert body["contents"][1]["parts"][0]["functionCall"]["args"] == {"value": [1]}
    assert transport.requests[0].url.params.get("x") == "1"


def test_empty_candidates() -> None:
    from mas.runtime.engine.llm_reasoning import coerce_reasoning_settings

    message = GeminiLLMProvider()._message_from_completion({}, coerce_reasoning_settings(None))
    assert message == {} or message.get("content") in {None, ""}


def test_on_stream_chunk_error_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = ['data: {"candidates":[{"content":{"parts":[{"text":"x"}]},"finishReason":"OTHER"}]}']
    provider = _provider(_Transport(lines=lines), monkeypatch, stream=True)

    def boom(_text: str) -> None:
        raise RuntimeError("cb")

    out = _chat(provider, model="m", messages=[{"role": "user", "content": "q"}], on_stream_chunk=boom)
    assert out["content"] == "x"
    assert out["finish_reason"] == "stop"


def test_sampling_top_k_and_async_stream_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _Transport()
    provider = _provider(transport, monkeypatch)
    _chat(
        provider,
        model="m",
        messages=[{"role": "user", "content": "q"}, {"role": "tool", "tool_call_id": "missing", "content": "1"}],
        sampling={"top_k": 3},
        extra_body={"cachedContent": "c"},
    )
    body = json.loads(transport.requests[0].content)
    assert body["generationConfig"]["topK"] == 3
    assert body["cachedContent"] == "c"
    streamed = _provider(_Transport(), monkeypatch, stream=True)
    streamed.inner.aclient = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _r: (_ for _ in ()).throw(httpx.ConnectError("down")))
    )
    with pytest.raises(ClassifiedFailure):
        asyncio.run(streamed.achat_completion(model="m", messages=[{"role": "user", "content": "q"}]))


def test_non_dict_parts_nameless_tool_choice_and_usage_without_total() -> None:
    from mas.library.standard.plugins.llm.gemini import _StreamAcc, _tool_config, _usage
    from mas.runtime.engine.llm_reasoning import coerce_reasoning_settings

    assert _tool_config({"function": {}}) is None
    usage = _usage({"promptTokenCount": 2, "candidatesTokenCount": 3})
    assert usage["total_tokens"] == 5
    message = GeminiLLMProvider()._message_from_completion(
        {"candidates": [{"content": {"parts": ["skip", {"text": "ok"}]}, "finishReason": "STOP"}]},
        coerce_reasoning_settings(None),
    )
    assert message["content"] == "ok"
    acc = _StreamAcc()
    acc.feed('data: {"candidates":[]}', None)
    acc.feed(
        'data: {"candidates":[{"content":{"parts":["x", {"text":"y"}]},"finishReason":"MAX_TOKENS"}]}',
        None,
    )
    out = acc.message(coerce_reasoning_settings(None))
    assert out["content"] == "y"
    assert out["finish_reason"] == "length"


def test_function_call_replay_carries_the_skip_signature_and_tools_are_unique(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _Transport()
    tool = {"type": "function", "function": {"name": "t", "parameters": {"type": "object"}}}
    _chat(
        _provider(transport, monkeypatch),
        model="m",
        tools=[tool, tool],
        messages=[
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c", "type": "function", "function": {"name": "t", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "c", "content": "{}"},
        ],
    )
    body = json.loads(transport.requests[0].content)
    assert body["contents"][1]["parts"][0]["thoughtSignature"] == "skip_thought_signature_validator"
    assert len(body["tools"][0]["functionDeclarations"]) == 1


def test_error_body_on_http_200_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    err = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota"}}
    provider = _provider(_Transport(err), monkeypatch)
    with pytest.raises(ClassifiedFailure) as exc:
        _chat(provider, model="m", messages=[{"role": "user", "content": "q"}])
    assert exc.value.failure_class is FailureClass.TRANSIENT
    assert exc.value.code == "RESOURCE_EXHAUSTED"


def test_blocked_prompt_is_a_content_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"promptFeedback": {"blockReason": "SAFETY"}}
    out = _chat(_provider(_Transport(body), monkeypatch), model="m", messages=[{"role": "user", "content": "q"}])
    assert out["finish_reason"] == "content_filter"
