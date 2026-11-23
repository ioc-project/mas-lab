#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Azure OpenAI protocol layer over a fake http transport."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from mas.library.standard.plugins.llm.azure import AzureLLMProvider
from mas.library.standard.plugins.llm.openai import OpenAILLMProvider

_OK = {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}


class _Transport:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        handler = httpx.MockTransport(self._answer)
        self.client = httpx.Client(transport=handler)
        self.aclient = httpx.AsyncClient(transport=handler)

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json=_OK)


def _chat(provider: AzureLLMProvider, **kwargs: Any) -> dict[str, Any]:
    return asyncio.run(provider.achat_completion(**kwargs))


def test_classic_deployment_path_posts_api_key() -> None:
    transport = _Transport()
    provider = AzureLLMProvider.wrap(
        transport,
        {
            "api_base": "https://demo.openai.azure.com",
            "api_key": {"secret": "azure"},
            "deployment": "gpt-4o",
            "api_version": "2024-10-21",
        },
    )
    out = _chat(provider, model="ignored", messages=[{"role": "user", "content": "q"}])
    req = transport.requests[0]
    assert req.url.path.endswith("/openai/deployments/gpt-4o/chat/completions")
    assert req.url.params.get("api-version") == "2024-10-21"
    assert req.headers["api-key"] == "secret"
    assert "Authorization" not in req.headers
    assert json.loads(req.content)["messages"][0]["content"] == "q"
    assert out["content"] == "ok"


def test_v1_host_keeps_chat_completions() -> None:
    transport = _Transport()
    provider = AzureLLMProvider.wrap(
        transport,
        {"api_base": "https://demo.services.ai.azure.com/openai/v1", "api_key": {"secret": "azure"}},
    )
    _chat(provider, model="gpt-4o", messages=[{"role": "user", "content": "q"}])
    req = transport.requests[0]
    assert req.url.path.endswith("/chat/completions")
    assert "deployments" not in req.url.path
    assert req.headers["api-key"] == "secret"
    assert not req.url.params


def test_deployment_defaults_to_the_model_name() -> None:
    transport = _Transport()
    provider = AzureLLMProvider.wrap(
        transport, {"api_base": "https://demo.openai.azure.com", "api_key": {"secret": "azure"}}
    )
    _chat(provider, model="gpt-4o-mini", messages=[{"role": "user", "content": "q"}])
    assert transport.requests[0].url.path.endswith("/openai/deployments/gpt-4o-mini/chat/completions")


def test_protocol_layers_is_http_then_azure() -> None:
    steps = AzureLLMProvider.protocol_layers({"api_base": "https://demo.openai.azure.com", "timeout": 9})
    assert [step["layer"] for step in steps] == ["http", "azure"]
    assert steps[0]["params"]["url"] == "https://demo.openai.azure.com"
    assert issubclass(AzureLLMProvider, OpenAILLMProvider)


def test_nested_proxy_auth_bearer_sends_authorization() -> None:
    transport = _Transport()
    provider = AzureLLMProvider.wrap(
        transport,
        {
            "proxy": {
                "api_base": "https://demo.openai.azure.com",
                "api_key": {"secret": "azure"},
                "deployment": "gpt-4o",
                "auth": "bearer",
            }
        },
    )
    _chat(provider, model="ignored", messages=[{"role": "user", "content": "q"}])
    req = transport.requests[0]
    assert req.url.path.endswith("/openai/deployments/gpt-4o/chat/completions")
    assert req.headers["Authorization"] == "Bearer secret"
    assert "api-key" not in req.headers


def test_auth_bearer_sends_authorization() -> None:
    transport = _Transport()
    provider = AzureLLMProvider.wrap(
        transport,
        {
            "api_base": "https://demo.openai.azure.com",
            "api_key": {"secret": "azure"},
            "deployment": "gpt-4o",
            "auth": "bearer",
        },
    )
    _chat(provider, model="ignored", messages=[{"role": "user", "content": "q"}])
    req = transport.requests[0]
    assert req.headers["Authorization"] == "Bearer secret"
    assert "api-key" not in req.headers


@pytest.mark.no_secrets
def test_missing_key_is_runtime_error() -> None:
    provider = AzureLLMProvider.wrap(
        _Transport(), {"api_base": "https://demo.openai.azure.com", "api_key": {"secret": "azure"}}
    )
    with pytest.raises(RuntimeError, match="Missing API key"):
        _chat(provider, model="m", messages=[{"role": "user", "content": "q"}])


def test_v1_detection_looks_at_path_segments_only() -> None:
    from mas.library.standard.plugins.llm.azure import _is_v1

    assert _is_v1("https://x.openai.azure.com/openai/v1")
    assert _is_v1("https://x.openai.azure.com/openai/v1/")
    assert not _is_v1("https://v1.example.com/openai")
    assert not _is_v1("https://x.openai.azure.com/api/v10")
