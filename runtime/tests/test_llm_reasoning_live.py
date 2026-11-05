#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Live Chat Completions against Outshift LiteLLM and GLS vLLM.

Skipped unless credentials are present. Network failures skip rather than fail CI.
Does not print API keys.
"""

from __future__ import annotations

import os
import socket
from urllib.parse import urlparse

import pytest
from mas.library.standard.plugins.llm.openai import OpenAILLMProvider
from mas.runtime.engine.llm_reasoning import sanitize_assistant_message


def _host(url: str) -> str:
    return str(urlparse(url).hostname or "")


def _host_resolves(host: str) -> bool:
    if not host:
        return False
    try:
        socket.getaddrinfo(host, 443)
        return True
    except OSError:
        return False


def _litellm_endpoint() -> tuple[str, str] | None:
    base = (
        os.environ.get("LITELLM_PROXY_API_BASE")
        or os.environ.get("LITELLM_URL")
        or os.environ.get("LITELLM_BASE_URL")
        or ""
    ).strip()
    key = (
        os.environ.get("OPENAI_API_KEY") or os.environ.get("LITELLM_API_KEY") or os.environ.get("LITELLM_USER_ID") or ""
    ).strip()
    if not base or not key:
        return None
    if _host(base) in {"api.openai.com", "example.com"}:
        return None
    return base.rstrip("/"), key


def _gls_endpoint() -> tuple[str, str] | None:
    key = (os.environ.get("OUTSHIFT_GLS_API_KEY") or "").strip()
    if not key:
        return None
    base = (
        os.environ.get("OUTSHIFT_GLS_API_BASE")
        or os.environ.get("GLS_VLLM_API_BASE")
        or os.environ.get("LLM_PROXY_API_BASE")
        or "https://vllm.outshift-gls.cisco.com/v1"
    ).strip()
    return base.rstrip("/"), key


def _chat(base: str, key: str, model: str, reasoning: dict) -> dict:
    provider = OpenAILLMProvider(api_base=base, reasoning=reasoning)
    return provider.chat_completion(
        model=model,
        messages=[{"role": "user", "content": "Reply with the single word pong and nothing else."}],
        api_key=key,
        max_tokens=64,
        temperature=0,
    )


_PREFERRED_LIVE_MODELS = (
    "onprem/gemma4",
    "gpt-5-mini",
    "gpt-5",
    "lightning-ai/gemini-3-flash-preview",
    "vertex_ai/gemini-2.5-flash",
    "azure/gpt-4o-mini",
)


def _pick_model(base: str, key: str, fallback: str) -> str:
    import httpx

    url = base.rstrip("/") + "/models"
    try:
        resp = httpx.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=20.0)
        if resp.status_code in {401, 403, 404}:
            return fallback
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return fallback
    ids = [str(item.get("id") or "") for item in (data.get("data") or []) if isinstance(item, dict)]
    ids = [i for i in ids if i]
    for name in (fallback, *_PREFERRED_LIVE_MODELS):
        if name in ids:
            return name
    if ids:
        return ids[0]
    return fallback


@pytest.mark.timeout(60)
def test_litellm_outshift_reasoning_exclude_hides_cot() -> None:
    endpoint = _litellm_endpoint()
    if endpoint is None:
        pytest.skip("LITELLM_URL / OPENAI_API_KEY not set")
    base, key = endpoint
    if not _host_resolves(_host(base)):
        pytest.skip(f"LiteLLM host {_host(base)} does not resolve")
    model = _pick_model(base, key, "onprem/gemma4")
    try:
        message = _chat(base, key, model, {"exclude": True})
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"LiteLLM chat failed on {_host(base)}: {exc}")
    cleaned = sanitize_assistant_message(dict(message), exclude=True)
    text = str(cleaned.get("content") or "")
    assert "reasoning_content" not in cleaned
    assert "<think" not in text.lower()


@pytest.mark.timeout(60)
def test_litellm_outshift_rejects_or_accepts_think_explicitly() -> None:
    """Do not retry-without-think: a proxy that rejects think must skip, not pass."""
    endpoint = _litellm_endpoint()
    if endpoint is None:
        pytest.skip("LITELLM_URL / OPENAI_API_KEY not set")
    base, key = endpoint
    if not _host_resolves(_host(base)):
        pytest.skip(f"LiteLLM host {_host(base)} does not resolve")
    model = _pick_model(base, key, "onprem/gemma4")
    from mas.runtime.engine.llm_model_catalog import default_model_catalog

    info = default_model_catalog().get(model)
    if info is not None and not info.supports("think"):
        pytest.skip(f"{model} does not advertise think")
    try:
        message = _chat(base, key, model, {"think": True, "exclude": True})
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"LiteLLM proxy rejected think on {_host(base)} ({model}): {exc}")
    cleaned = sanitize_assistant_message(dict(message), exclude=True)
    assert "reasoning_content" not in cleaned
    assert "<think" not in str(cleaned.get("content") or "").lower()


@pytest.mark.timeout(60)
def test_gls_vllm_think_flag_and_local_exclude() -> None:
    endpoint = _gls_endpoint()
    if endpoint is None:
        pytest.skip("OUTSHIFT_GLS_API_KEY not set")
    base, key = endpoint
    host = _host(base)
    if not _host_resolves(host):
        pytest.skip(f"GLS vLLM host {host} does not resolve (set OUTSHIFT_GLS_API_BASE)")
    model = _pick_model(base, key, "gemma-4-26b-a4b-it-node1-a100-0")
    try:
        message = _chat(base, key, model, {"think": True, "exclude": True})
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"GLS vLLM chat failed on {host}: {exc}")
    cleaned = sanitize_assistant_message(dict(message), exclude=True)
    text = str(cleaned.get("content") or "")
    assert "<think" not in text.lower()
    assert "reasoning_content" not in cleaned
