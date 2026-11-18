#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Named openai-compatible hosts inherit the openai chat-completions layer."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from mas.library.standard.plugins.llm.compatible import (
    CerebrasLLMProvider,
    DatabricksLLMProvider,
    GroqLLMProvider,
    HuggingFaceLLMProvider,
    LiteLLMLLMProvider,
    MistralLLMProvider,
    OpenAICompatibleLLMProvider,
    OpenRouterLLMProvider,
    VllmLLMProvider,
    XaiLLMProvider,
)
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


@pytest.mark.parametrize(
    ("cls", "kind", "host", "env_name"),
    [
        (GroqLLMProvider, "groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY"),
        (OpenRouterLLMProvider, "openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
        (XaiLLMProvider, "xai", "https://api.x.ai/v1", "XAI_API_KEY"),
        (LiteLLMLLMProvider, "litellm", "http://localhost:4000/v1", "LITELLM_API_KEY"),
        (CerebrasLLMProvider, "cerebras", "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY"),
        (MistralLLMProvider, "mistral", "https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
        (VllmLLMProvider, "vllm", "http://localhost:8000/v1", "VLLM_API_KEY"),
        (HuggingFaceLLMProvider, "huggingface", "https://router.huggingface.co/v1", "HF_TOKEN"),
    ],
)
def test_named_host_inherits_openai(cls: type[OpenAILLMProvider], kind: str, host: str, env_name: str) -> None:
    assert issubclass(cls, OpenAICompatibleLLMProvider)
    assert issubclass(cls, OpenAILLMProvider)
    assert cls.kind == kind
    assert cls.default_api_base == host
    assert cls.default_api_key_env == env_name
    steps = cls.protocol_layers({"api_base": "http://127.0.0.1:9/v1"})
    assert [step["layer"] for step in steps] == ["http", kind]
    transport = _Transport()
    provider = cls.wrap(transport, {"api_base": "http://127.0.0.1:9/v1", "api_key": "secret"})
    out = asyncio.run(provider.achat_completion(model="m", messages=[{"role": "user", "content": "q"}]))
    req = transport.requests[0]
    assert req.url.path.endswith("/chat/completions")
    assert req.headers["Authorization"] == "Bearer secret"
    assert out["content"] == "ok"


@pytest.mark.parametrize("cls", [GroqLLMProvider, CerebrasLLMProvider, MistralLLMProvider])
def test_hosts_that_reject_max_completion_tokens_send_max_tokens(cls: type[OpenAILLMProvider]) -> None:
    transport = _Transport()
    provider = cls.wrap(transport, {"api_base": "http://127.0.0.1:9/v1", "api_key": "secret"})
    asyncio.run(
        provider.achat_completion(
            model="m",
            messages=[{"role": "user", "content": "q"}],
            max_completion_tokens=32,
        )
    )
    body = json.loads(transport.requests[0].content)
    assert body["max_tokens"] == 32
    assert "max_completion_tokens" not in body


def test_databricks_posts_invocations_path() -> None:
    transport = _Transport()
    provider = DatabricksLLMProvider.wrap(
        transport,
        {"api_base": "https://demo.cloud.databricks.com", "api_key": "secret"},
    )
    out = asyncio.run(provider.achat_completion(model="chat-ep", messages=[{"role": "user", "content": "q"}]))
    req = transport.requests[0]
    assert req.url.path.endswith("/serving-endpoints/chat-ep/invocations")
    assert req.headers["Authorization"] == "Bearer secret"
    assert json.loads(req.content)["messages"][0]["content"] == "q"
    assert out["content"] == "ok"
    steps = DatabricksLLMProvider.protocol_layers({"api_base": "https://demo.cloud.databricks.com"})
    assert [step["layer"] for step in steps] == ["http", "databricks"]
    assert issubclass(DatabricksLLMProvider, OpenAICompatibleLLMProvider)


def test_databricks_missing_host_is_runtime_error() -> None:
    provider = DatabricksLLMProvider.wrap(_Transport(), {"api_key": "secret"})
    with pytest.raises(RuntimeError, match="api_base"):
        asyncio.run(provider.achat_completion(model="chat-ep", messages=[{"role": "user", "content": "q"}]))
