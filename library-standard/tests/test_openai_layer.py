#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``openai`` as a protocol layer over an http transport: the transport's clients carry the calls."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from mas.library.standard.plugins.llm.openai import OpenAILLMProvider
from mas.runtime.engine.infra_pipeline import _PROVIDER_CALL

_BODY = {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}


class _Transport:
    """What the http layer offers a protocol layer: clients, and a way to drop them after a failure."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.discarded = 0
        self.adiscarded = 0
        self._handler = httpx.MockTransport(self._answer)
        self.client = httpx.Client(transport=self._handler)
        self.aclient = httpx.AsyncClient(transport=self._handler)

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json=_BODY)

    def discard_client(self) -> None:
        self.discarded += 1

    async def adiscard_aclient(self) -> None:
        self.adiscarded += 1


def _provider(transport: _Transport, monkeypatch: pytest.MonkeyPatch, **params: Any) -> OpenAILLMProvider:
    del monkeypatch
    return OpenAILLMProvider.wrap(
        transport,
        {"api_base": "http://127.0.0.1:9/v1", "api_key": "secret", "api_key_env": "K", **params},
        call_kinds=("llm",),
    )


def test_an_async_chat_goes_through_the_transport_aclient(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _Transport()
    provider = _provider(transport, monkeypatch)
    out = asyncio.run(provider.achat_completion(model="m", messages=[{"role": "user", "content": "q"}]))
    assert out["content"] == "ok"
    assert len(transport.requests) == 1


def test_a_transport_failure_drops_the_transport_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _Transport()
    provider = _provider(transport, monkeypatch)
    asyncio.run(provider._adiscard_client_after_transport_failure(httpx.ConnectError("down")))
    asyncio.run(provider._adiscard_client_after_transport_failure(ValueError("not transport")))
    assert transport.adiscarded == 1


def test_protocol_layers_is_http_then_openai_with_the_provider_timeout_and_tls() -> None:
    steps = OpenAILLMProvider.protocol_layers({"api_base": "http://127.0.0.1:9/v1"})
    assert [step["layer"] for step in steps] == ["http", "openai"]
    assert steps[0]["params"] == {"url": "http://127.0.0.1:9/v1", "timeout": 120.0}
    tls = OpenAILLMProvider.protocol_layers(
        {"proxy": {"api_base": "https://x/v1", "timeout": 5, "verify_ssl": False}, "ca_bundle": "/ca.pem"}
    )
    assert tls[0]["params"] == {"url": "https://x/v1", "timeout": 5.0, "verify_ssl": False, "ca_bundle": "/ca.pem"}
    assert "proxy" not in tls[1]["params"]


def _io() -> Any:
    return SimpleNamespace(correlation_id=3)


def test_the_openai_layer_answers_the_chat_in_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _Transport()
    provider = _provider(transport, monkeypatch)
    payload = {"model": "m", "messages": [{"role": "user", "content": "q"}]}
    token = _PROVIDER_CALL.set(payload)
    try:
        ret = asyncio.run(provider.ainvoke(_io()))
        assert payload["_result"]["content"] == "ok" and '"content": "ok"' in ret.text
    finally:
        _PROVIDER_CALL.reset(token)


def test_structured_response_format_sets_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    import json as json_lib

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"n": 1}'}, "finish_reason": "stop"}]})

    transport = _Transport()
    transport.aclient = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = _provider(transport, monkeypatch)
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    out = asyncio.run(
        provider.achat_completion(
            model="m",
            messages=[{"role": "user", "content": "q"}],
            response_format={"type": "json_schema", "json_schema": {"name": "r", "schema": schema}},
        )
    )
    assert json_lib.loads(captured[0].content)["response_format"]["json_schema"]["schema"] == schema
    assert out["parsed"] == {"n": 1}


def test_the_openai_layer_needs_a_chat_in_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(_Transport(), monkeypatch)
    with pytest.raises(RuntimeError, match="in flight"):
        asyncio.run(provider.ainvoke(_io()))
