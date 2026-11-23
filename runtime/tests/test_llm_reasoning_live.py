#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Chat Completions against an OpenAI-compatible endpoint that reasons (LiteLLM, vLLM).

The endpoint is a fake: an ``httpx.MockTransport`` answers in the shape those servers use
(``reasoning_content`` next to the answer, a ``<think>`` block inside it). Nothing leaves the
process, so the tests need no host, key or network.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from mas.library.standard.plugins.llm.openai import OpenAILLMProvider
from mas.runtime.engine.llm_reasoning import sanitize_assistant_message

_GLS_BASE = "https://vllm.gls.invalid/v1"
_LITELLM_BASE = "https://litellm.invalid/v1"
_COT_MESSAGE = {
    "role": "assistant",
    "content": "<think>The user wants one word.</think>pong",
    "reasoning_content": "The user wants one word.",
}


class FakeEndpoint:
    """Records every request and answers each with ``respond(request body)``."""

    def __init__(self, respond: Any) -> None:
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict[str, Any]] = []
        self._respond = respond

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        self.bodies.append(body)
        return self._respond(body)


def _answer(message: dict[str, Any]) -> Any:
    return lambda _body: httpx.Response(200, json={"choices": [{"message": message}]})


def _provider(base: str, endpoint: FakeEndpoint, reasoning: dict[str, Any]) -> OpenAILLMProvider:
    provider = OpenAILLMProvider(api_base=base, api_key={"secret": "openai"}, reasoning=reasoning)
    provider._aclient = httpx.AsyncClient(transport=httpx.MockTransport(endpoint))
    return provider


async def _chat(provider: OpenAILLMProvider, model: str) -> dict[str, Any]:
    return await provider.achat_completion(
        model=model,
        messages=[{"role": "user", "content": "Reply with the single word pong and nothing else."}],
        max_tokens=64,
        temperature=0,
    )


async def test_litellm_reasoning_exclude_hides_cot() -> None:
    endpoint = FakeEndpoint(_answer(_COT_MESSAGE))
    message = await _chat(_provider(_LITELLM_BASE, endpoint, {"exclude": True}), "onprem/gemma4")
    cleaned = sanitize_assistant_message(dict(message), exclude=True)
    assert cleaned["content"] == "pong"
    assert "reasoning_content" not in cleaned
    (request,) = endpoint.requests
    assert str(request.url) == f"{_LITELLM_BASE}/chat/completions"
    assert request.headers["authorization"] == "Bearer secret"
    assert "chat_template_kwargs" not in endpoint.bodies[0]
    assert "think" not in endpoint.bodies[0]


async def test_litellm_rejecting_think_is_an_error_not_a_silent_retry_without_it() -> None:
    """A proxy that rejects ``think`` fails the call: one request, no retry without think."""
    from mas.runtime.reliability.classes import ClassifiedFailure

    endpoint = FakeEndpoint(lambda _body: httpx.Response(400, json={"error": {"message": "unknown parameter: think"}}))
    provider = _provider(_LITELLM_BASE, endpoint, {"think": True, "exclude": True})
    with pytest.raises(ClassifiedFailure):
        await _chat(provider, "onprem/gemma4")
    assert len(endpoint.bodies) == 1
    assert endpoint.bodies[0]["chat_template_kwargs"] == {"enable_thinking": True}


async def test_litellm_accepting_think_sends_it_and_hides_the_cot() -> None:
    endpoint = FakeEndpoint(_answer(_COT_MESSAGE))
    message = await _chat(_provider(_LITELLM_BASE, endpoint, {"think": True, "exclude": True}), "onprem/gemma4")
    cleaned = sanitize_assistant_message(dict(message), exclude=True)
    assert endpoint.bodies[0]["chat_template_kwargs"] == {"enable_thinking": True}
    assert "reasoning_content" not in cleaned
    assert "<think" not in str(cleaned.get("content") or "").lower()


async def test_gls_vllm_think_flag_and_local_exclude() -> None:
    """``think`` is sent as the thinking switch; the chain of thought that comes back is dropped."""
    endpoint = FakeEndpoint(_answer(_COT_MESSAGE))
    message = await _chat(
        _provider(_GLS_BASE, endpoint, {"think": True, "exclude": True}),
        "gemma-4-26b-a4b-it-node1-a100-0",
    )
    (request,) = endpoint.requests
    assert str(request.url) == f"{_GLS_BASE}/chat/completions"
    assert endpoint.bodies[0]["chat_template_kwargs"] == {"enable_thinking": True}
    assert endpoint.bodies[0]["model"] == "gemma-4-26b-a4b-it-node1-a100-0"
    cleaned = sanitize_assistant_message(dict(message), exclude=True)
    assert cleaned["content"] == "pong"
    assert "<think" not in str(cleaned.get("content") or "").lower()
    assert "reasoning_content" not in cleaned
