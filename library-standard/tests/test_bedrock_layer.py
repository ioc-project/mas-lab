#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Bedrock Converse protocol layer over a fake http transport."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
from mas.library.standard.plugins.llm.bedrock import BedrockLLMProvider
from mas.library.standard.plugins.llm.http_protocol import HttpLlmProtocol

_OK = {
    "output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}},
    "stopReason": "end_turn",
    "usage": {"inputTokens": 4, "outputTokens": 2},
}


class _Transport:
    def __init__(self, body: dict[str, Any] | None = None, *, status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self.body = body if body is not None else _OK
        self.status = status
        handler = httpx.MockTransport(self._answer)
        self.client = httpx.Client(transport=handler)
        self.aclient = httpx.AsyncClient(transport=handler)

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status >= 400:
            return httpx.Response(self.status, json={"message": "nope"}, request=request)
        return httpx.Response(200, json=self.body)


def _chat(provider: BedrockLLMProvider, **kwargs: Any) -> dict[str, Any]:
    return asyncio.run(provider.achat_completion(**kwargs))


def test_converse_maps_messages_tools_and_usage() -> None:
    transport = _Transport(
        {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"text": "hi"},
                        {"toolUse": {"toolUseId": "t1", "name": "echo", "input": {"q": 1}}},
                    ],
                }
            },
            "stopReason": "tool_use",
            "usage": {"inputTokens": 9, "outputTokens": 3},
        }
    )
    provider = BedrockLLMProvider.wrap(
        transport,
        {"api_base": "http://127.0.0.1:9", "api_key": "secret"},
    )
    out = _chat(
        provider,
        model="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
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
    assert unquote(req.url.path).endswith("/model/us.anthropic.claude-sonnet-4-5-20250929-v1:0/converse")
    assert req.headers["Authorization"] == "Bearer secret"
    body = json.loads(req.content)
    assert body["system"] == [{"text": "sys"}]
    assert body["inferenceConfig"]["maxTokens"] == 16
    assert body["toolConfig"]["toolChoice"] == {"any": {}}
    assert body["toolConfig"]["tools"][0]["toolSpec"]["name"] == "echo"
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][1]["content"][0]["toolUse"]["name"] == "echo"
    assert body["messages"][2]["content"][0]["toolResult"]["toolUseId"] == "c1"
    assert out["content"] == "hi"
    assert out["tool_calls"][0]["function"]["name"] == "echo"
    assert out["finish_reason"] == "tool_calls"
    assert out["usage"]["prompt_tokens"] == 9
    assert out["usage"]["total_tokens"] == 12


def test_protocol_layers_is_http_then_bedrock() -> None:
    steps = BedrockLLMProvider.protocol_layers({"api_base": "http://127.0.0.1:9", "timeout": 7})
    assert [step["layer"] for step in steps] == ["http", "bedrock"]
    assert steps[0]["params"] == {"url": "http://127.0.0.1:9", "timeout": 7.0}
    assert issubclass(BedrockLLMProvider, HttpLlmProtocol)


def test_missing_key_is_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "from-env")
    provider = BedrockLLMProvider.wrap(_Transport(), {"api_base": "http://127.0.0.1:9"})
    with pytest.raises(RuntimeError, match="Missing API key"):
        _chat(provider, model="m", messages=[{"role": "user", "content": "q"}])


def test_stream_emits_the_full_converse_text() -> None:
    received: list[str] = []
    transport = _Transport()
    provider = BedrockLLMProvider.wrap(
        transport,
        {"api_base": "http://127.0.0.1:9", "api_key": "secret", "stream": True},
    )
    out = _chat(
        provider,
        model="m",
        messages=[{"role": "user", "content": "q"}],
        on_stream_chunk=received.append,
    )
    assert received == ["ok"]
    assert out["content"] == "ok"
    assert transport.requests[0].url.path.endswith("/converse")


def test_converse_alternates_roles_drops_blanks_and_honours_tool_choice_none() -> None:
    transport = _Transport()
    provider = BedrockLLMProvider.wrap(
        transport, {"api_base": "http://127.0.0.1:9", "api_key": "k", "api_key_env": "K"}, call_kinds=("llm",)
    )
    _chat(
        provider,
        model="m",
        messages=[
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "r"},
            {"role": "user", "content": "next"},
            {"role": "assistant", "content": ""},
        ],
        tools=[{"type": "function", "function": {"name": "t", "parameters": {"type": "object"}}}],
        tool_choice="none",
    )
    body = json.loads(transport.requests[0].content)
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
    assert body["messages"][2]["content"][-1] == {"text": "next"}
    # The history holds tool blocks, so Converse still needs the tool config, with no tool choice.
    assert "toolChoice" not in body["toolConfig"] and body["toolConfig"]["tools"]


def test_tool_choice_none_without_tool_history_omits_the_tools() -> None:
    transport = _Transport()
    provider = BedrockLLMProvider.wrap(
        transport, {"api_base": "http://127.0.0.1:9", "api_key": "k", "api_key_env": "K"}, call_kinds=("llm",)
    )
    _chat(
        provider,
        model="m",
        messages=[{"role": "user", "content": "q"}],
        tools=[{"type": "function", "function": {"name": "t", "parameters": {"type": "object"}}}],
        tool_choice="none",
    )
    assert "toolConfig" not in json.loads(transport.requests[0].content)
