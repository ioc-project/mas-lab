#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""An LLM layer reveals its key per request: a rotated key is used by the next request, after a call neither
the layer nor any client holds it, and an optional key that is not set sends no auth header."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from mas.library.standard.plugins.llm import http_protocol
from mas.library.standard.plugins.llm.anthropic import AnthropicLLMProvider
from mas.library.standard.plugins.llm.gemini import GeminiLLMProvider
from mas.library.standard.plugins.llm.openai import OpenAILLMProvider
from mas.runtime.secrets import SecretResolver, set_process_resolver
from secret_fakes import MemorySecretStore

_CASES = {
    "openai": (OpenAILLMProvider, "/v1", "authorization", "Bearer "),
    "anthropic": (AnthropicLLMProvider, "/v1", "x-api-key", ""),
    "gemini": (GeminiLLMProvider, "/v1beta", "x-goog-api-key", ""),
}
_BODIES = {
    "anthropic": {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"},
    "gemini": {"candidates": [{"content": {"role": "model", "parts": [{"text": "ok"}]}, "finishReason": "STOP"}]},
    "openai": {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]},
}
_CALL = {"model": "m", "messages": [{"role": "user", "content": "q"}]}


def _clients(family: str, sent: list[httpx.Request], made: list[httpx.AsyncClient], monkeypatch) -> Any:
    real = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: sent.append(request) or httpx.Response(200, json=_BODIES[family]))

    def mocked(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        made.append(real(*args, **{**kwargs, "transport": transport}))
        return made[-1]

    monkeypatch.setattr(http_protocol.httpx, "AsyncClient", mocked)

    class Inner:
        aclient = real(transport=transport)

        async def adiscard_aclient(self) -> None:
            return None

    return Inner()


@pytest.mark.parametrize("family", sorted(_CASES))
def test_a_rotated_key_is_sent_next_and_held_nowhere(family: str, monkeypatch: pytest.MonkeyPatch) -> None:
    cls, path, header, prefix = _CASES[family]
    sent: list[httpx.Request] = []
    made: list[httpx.AsyncClient] = []
    inner = _clients(family, sent, made, monkeypatch)
    store = MemorySecretStore({family: {"value": "sk-one-4e1d"}})
    set_process_resolver(SecretResolver(store, ttl_s=3600.0))  # a long TTL: the store's change token rotates it
    row = {"api_base": f"http://127.0.0.1:9{path}", "api_key": {"secret": family}}
    layered = cls.wrap(inner, row, call_kinds=("llm",))
    asyncio.run(layered.achat_completion(**_CALL))
    store.put(family, "sk-two-4e1d")
    asyncio.run(layered.achat_completion(**_CALL))
    alone = None if family == "gemini" else cls.from_infra_spec(row)
    if alone is not None:
        asyncio.run(alone.achat_completion(**_CALL))
    assert [r.headers[header] for r in sent[:2]] == [f"{prefix}sk-one-4e1d", f"{prefix}sk-two-4e1d"]
    providers = [p for p in (layered, alone) if p is not None]
    held = [repr(p) + repr(vars(p)) for p in providers] + [repr(dict(c.headers)) for c in (inner.aclient, *made)]
    assert "4e1d" not in "\n".join(held)


def test_an_optional_key_that_is_not_set_sends_no_auth_header(monkeypatch: pytest.MonkeyPatch) -> None:
    # kills: an optional api_key that is missing raising, or an empty "Bearer " going out
    sent: list[httpx.Request] = []
    inner = _clients("openai", sent, [], monkeypatch)
    set_process_resolver(SecretResolver(MemorySecretStore({}), ttl_s=0.0))
    row = {"api_base": "http://localhost:11434/v1", "api_key": {"secret": {"name": "ollama", "optional": True}}}
    asyncio.run(OpenAILLMProvider.wrap(inner, row, call_kinds=("llm",)).achat_completion(**_CALL))
    assert "authorization" not in sent[0].headers
