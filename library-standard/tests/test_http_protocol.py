#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared HTTP protocol LLM helpers."""

from __future__ import annotations

import pytest
from mas.library.standard.plugins.llm.http_protocol import HttpLlmProtocol, protocol_http_steps
from mas.library.standard.plugins.llm.openai import OpenAILLMProvider
from mas.runtime.engine.llm_reasoning import coerce_reasoning_settings


def test_protocol_http_steps_read_proxy_and_url() -> None:
    steps = protocol_http_steps("anthropic", {"proxy": {"api_base": "http://x/v1", "timeout": 3, "verify_ssl": False}})
    assert steps[0]["params"] == {"url": "http://x/v1", "timeout": 3.0, "verify_ssl": False}
    assert protocol_http_steps("gemini", {"url": "http://g"})[0]["params"]["url"] == "http://g"


def test_timeout_prefers_http_timeout_then_default() -> None:
    assert OpenAILLMProvider(http_timeout=9)._timeout() == 9.0
    assert OpenAILLMProvider()._timeout() == 120.0


def test_from_infra_spec_url_and_from_provider_spec() -> None:
    provider = OpenAILLMProvider.from_infra_spec({"url": "http://127.0.0.1:9/v1", "api_key_env": "K"})
    assert provider.api_base == "http://127.0.0.1:9/v1"
    assert OpenAILLMProvider.from_provider_spec({"api_base": "http://x"}).api_base == "http://x"


def test_base_hooks_are_unimplemented() -> None:
    layer = HttpLlmProtocol()
    with pytest.raises(NotImplementedError):
        layer._build_chat_request()
    with pytest.raises(NotImplementedError):
        layer._message_from_completion({}, coerce_reasoning_settings(None))


@pytest.mark.asyncio
async def test_async_stream_hook_is_unimplemented() -> None:
    layer = HttpLlmProtocol()
    with pytest.raises(NotImplementedError):
        await layer._achat_completion_streamed(
            "u", {}, {}, on_stream_chunk=None, reasoning=coerce_reasoning_settings(None)
        )


def test_structured_message_uses_response_schema() -> None:
    message = {"content": '{"n": 1}'}
    payload = {"generationConfig": {"responseSchema": {"type": "object", "properties": {"n": {"type": "integer"}}}}}
    assert HttpLlmProtocol()._structured_message(message, payload)["parsed"] == {"n": 1}


def test_protocol_does_not_read_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    monkeypatch.setenv("K", "from-env")
    layer = OpenAILLMProvider.from_infra_spec({"url": "http://127.0.0.1:9/v1", "api_key_env": "K"})
    assert layer.api_key == ""
    with pytest.raises(RuntimeError, match="Missing API key"):
        asyncio.run(layer.achat_completion(model="m", messages=[{"role": "user", "content": "q"}]))
    held = OpenAILLMProvider.from_infra_spec({"url": "http://127.0.0.1:9/v1", "api_key": "secret"})
    assert held.api_key == "secret"
    assert "secret" not in repr(held)
