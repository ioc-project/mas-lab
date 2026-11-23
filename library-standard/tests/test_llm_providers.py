#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""LLMProvider plugins — openai protocol, cache wrapper, registry lookup."""

from __future__ import annotations

import json

import httpx
import pytest
from mas.library.standard.plugins.llm.cache import CacheLLMProvider
from mas.library.standard.plugins.llm.openai import OpenAILLMProvider
from mas.runtime.engine.infra_pipeline import wrap_llm_provider_pipeline
from mas.runtime.layers.defaults import merge_default_steps
from mas.runtime.registry.llm_provider_registry import (
    LLMProviderRegistry,
    iter_model_provider_specs,
    llm_provider_class,
    llm_provider_from_infra,
    llm_provider_kind,
    llm_providers_from_manifest,
    protocol_kind_from_infra,
)


def _retrying(provider):
    return wrap_llm_provider_pipeline(provider, merge_default_steps([], "llm"))


def _protocol(provider):
    current = provider
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, OpenAILLMProvider):
            return current
        current = getattr(current, "inner", None)
    return provider


class _StubInner:
    def __init__(self, message: dict, *, kind: str = "openai") -> None:
        self.message = message
        self.kind = kind
        self.calls = 0

    async def achat_completion(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        return dict(self.message)


async def test_a_sync_only_provider_still_works_on_a_worker_thread():
    import threading

    from mas.runtime.registry.llm_provider_protocol import MissingLLMProviderContract, require_achat_completion

    seen: list[str] = []

    class SyncOnly:
        def chat_completion(self, **kwargs):
            seen.append(threading.current_thread().name)
            return {"role": "assistant", "content": "ok"}

    class Neither:
        pass

    require_achat_completion(SyncOnly, name="sync-only")
    with pytest.raises(MissingLLMProviderContract):
        require_achat_completion(Neither, name="neither")

    registry = LLMProviderRegistry()
    registry.register_provider(SyncOnly())
    message = await registry.achat_completion(model="m", messages=[])
    assert message["content"] == "ok"
    assert seen and seen[0].startswith("mas-sync-plugin")


def test_llm_provider_kind_defaults_to_openai():
    assert llm_provider_kind({}) == "openai"
    assert llm_provider_kind({"protocol": "bedrock"}) == "bedrock"
    assert protocol_kind_from_infra({"protocol": "cache"}) == "openai"


def test_iter_model_specs_groups_by_protocol_kind():
    specs = iter_model_provider_specs(
        {
            "spec": {
                "models": [
                    {"model": "gpt-4o-mini", "kind": "openai"},
                    {"model": "claude-sonnet", "kind": "bedrock"},
                ]
            }
        },
        llm_proxy={"protocol": "openai", "api_base": "https://api.openai.com/v1"},
    )
    by_kind = {spec["kind"]: spec["models"] for spec in specs}
    assert by_kind["openai"] == ["gpt-4o-mini"]
    assert by_kind["bedrock"] == ["claude-sonnet"]


def test_iter_model_specs_rejects_cache_as_protocol():
    with pytest.raises(ValueError, match="not a wire protocol"):
        iter_model_provider_specs({"spec": {"models": [{"model": "m", "kind": "cache"}]}})


async def test_registry_routes_model_names_like_tools():
    openai = _StubInner({"role": "assistant", "content": "oai"}, kind="openai")
    bedrock = _StubInner({"role": "assistant", "content": "br"}, kind="bedrock")
    registry = LLMProviderRegistry()
    registry.register_provider(openai, models=["gpt-4o-mini"])
    registry.register_provider(bedrock, models=["claude-sonnet"])
    registry.initialize()
    assert registry.provider_for("gpt-4o-mini") is openai
    assert registry.provider_for("claude-sonnet") is bedrock
    assert (await registry.achat_completion(model="claude-sonnet", messages=[]))["content"] == "br"


def test_llm_providers_from_manifest_routes_two_openai_leaves():
    from mas.runtime.registry.llm_provider_registry import LLMProviderRegistry

    provider = llm_providers_from_manifest(
        {
            "spec": {
                "models": [
                    {"model": "provider-a/model-a", "kind": "openai"},
                    {"model": "judge-model", "kind": "openai"},
                ]
            }
        },
        llm_proxy={"api_base": "https://arm.example/v1"},
        llm_proxies={
            "arm": {
                "api_base": "https://arm.example/v1",
                "allowed": ["provider-a/model-a"],
                "protocol": "openai",
            },
            "judge": {
                "api_base": "https://judge.example/v1",
                "allowed": ["judge-model"],
                "protocol": "openai",
            },
        },
    )
    assert isinstance(provider, LLMProviderRegistry)
    arm = provider.provider_for("provider-a/model-a")
    judge = provider.provider_for("judge-model")
    assert arm.api_base == "https://arm.example/v1"
    assert judge.api_base == "https://judge.example/v1"
    assert arm is not judge


def test_iter_model_specs_claims_canonical_and_alias():
    specs = iter_model_provider_specs(
        {"spec": {"models": [{"model": "default", "kind": "openai"}]}},
        llm_proxies={
            "arm": {
                "api_base": "https://arm.example/v1",
                "allowed": ["real-a"],
                "mappings": {"default": "real-a"},
                "protocol": "openai",
            },
            "judge": {
                "api_base": "https://judge.example/v1",
                "allowed": ["judge-model"],
                "protocol": "openai",
            },
        },
    )
    arm = next(spec for spec in specs if spec.get("api_base") == "https://arm.example/v1")
    claimed = arm["models"]
    assert "default" in claimed
    assert "real-a" in claimed
    from mas.runtime.registry.llm_provider_registry import LLMProviderRegistry

    provider = llm_providers_from_manifest(
        {
            "spec": {
                "models": [
                    {"model": "default", "kind": "openai"},
                    {"model": "judge-model", "kind": "openai"},
                ]
            }
        },
        llm_proxies={
            "arm": {
                "api_base": "https://arm.example/v1",
                "allowed": ["real-a"],
                "mappings": {"default": "real-a"},
                "protocol": "openai",
            },
            "judge": {
                "api_base": "https://judge.example/v1",
                "allowed": ["judge-model"],
                "protocol": "openai",
            },
        },
    )
    assert isinstance(provider, LLMProviderRegistry)
    assert provider.provider_for("real-a").api_base == "https://arm.example/v1"
    assert provider.provider_for("default").api_base == "https://arm.example/v1"
    assert provider.provider_for("judge-model").api_base == "https://judge.example/v1"


def test_llm_providers_from_manifest_wraps_per_leaf_pipeline():
    from mas.runtime.engine.infra_pipeline import PipedLLMProvider
    from mas.runtime.registry.llm_provider_registry import LLMProviderRegistry

    provider = llm_providers_from_manifest(
        None,
        llm_proxies={
            "arm": {
                "api_base": "https://arm.example/v1",
                "allowed": ["provider-a/model-a"],
                "protocol": "openai",
                "pipeline": [{"layer": "fault_inject", "params": {"rate": 1.0}}],
            },
            "judge": {
                "api_base": "https://judge.example/v1",
                "allowed": ["judge-model"],
                "protocol": "openai",
                "pipeline": [{"layer": "llm_scheduler", "params": {"max_in_flight": 2}}],
            },
        },
    )
    assert isinstance(provider, LLMProviderRegistry)
    arm = provider.provider_for("provider-a/model-a")
    judge = provider.provider_for("judge-model")
    assert isinstance(arm, PipedLLMProvider)
    assert isinstance(judge, PipedLLMProvider)
    assert arm is not judge
    from mas.runtime.layers.compose import layer_id

    assert [layer_id(step) for step in arm.pipeline] == ["retry", "fault_inject"]
    assert [layer_id(step) for step in judge.pipeline] == ["retry", "llm_scheduler"]
    scheduler = judge._stack
    seen: set[int] = set()
    while scheduler is not None and id(scheduler) not in seen:
        seen.add(id(scheduler))
        policy = getattr(scheduler, "policy", None)
        if policy is not None and hasattr(policy, "max_in_flight"):
            assert policy.max_in_flight == 2
            break
        scheduler = getattr(scheduler, "inner", None)
    else:
        raise AssertionError("scheduler layer missing")


def test_llm_providers_from_manifest_uses_models_kind():
    provider = llm_providers_from_manifest(
        {"spec": {"models": [{"model": "gpt-4o-mini", "kind": "openai"}]}},
        llm_proxy={"api_base": "https://api.openai.com/v1"},
    )
    provider = _protocol(provider)
    assert isinstance(provider, OpenAILLMProvider)
    assert provider.api_base == "https://api.openai.com/v1"
    assert provider.api_key.name == "openai"


def test_registry_resolves_openai_and_cache():
    from mas.library.standard.plugins.llm.anthropic import AnthropicLLMProvider
    from mas.library.standard.plugins.llm.azure import AzureLLMProvider
    from mas.library.standard.plugins.llm.bedrock import BedrockLLMProvider
    from mas.library.standard.plugins.llm.compatible import GroqLLMProvider
    from mas.library.standard.plugins.llm.gemini import GeminiLLMProvider
    from mas.runtime.registry import get_registry

    assert llm_provider_class("openai") is OpenAILLMProvider
    assert llm_provider_class("cache") is CacheLLMProvider
    assert llm_provider_class("anthropic") is AnthropicLLMProvider
    assert llm_provider_class("gemini") is GeminiLLMProvider
    assert llm_provider_class("azure") is AzureLLMProvider
    assert llm_provider_class("bedrock") is BedrockLLMProvider
    assert llm_provider_class("groq") is GroqLLMProvider
    layer = get_registry().resolve_by_type("layer", "openai")
    assert layer is not None
    assert layer.load_class() is OpenAILLMProvider
    assert get_registry().resolve_by_type("layer", "anthropic").load_class() is AnthropicLLMProvider
    assert get_registry().resolve_by_type("layer", "gemini").load_class() is GeminiLLMProvider
    assert get_registry().resolve_by_type("layer", "azure").load_class() is AzureLLMProvider
    assert get_registry().resolve_by_type("layer", "bedrock").load_class() is BedrockLLMProvider
    assert get_registry().resolve_by_type("layer", "groq").load_class() is GroqLLMProvider


def test_factory_does_not_copy_an_api_key_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from mas.runtime.registry.llm_provider_registry import apply_llm_endpoint_defaults, instantiate_llm_provider
    from mas.runtime.secrets.ref import SecretRef

    monkeypatch.setenv("OPENAI_API_KEY", "from-env")
    spec = apply_llm_endpoint_defaults({"kind": "openai"}, {"api_base": "https://example.test/v1"})
    assert "api_key" not in spec or spec.get("api_key") != "from-env"
    provider = instantiate_llm_provider("openai", spec)
    assert provider.api_key is None  # example.test is not OpenAI's endpoint: no default secret either
    assert "from-env" not in repr(provider)
    assert instantiate_llm_provider("openai", {"api_base": "https://api.openai.com/v1"}).api_key == SecretRef(
        name="openai"
    )


def test_factory_uses_the_protocol_default_secret_only_for_its_own_endpoint() -> None:
    # kills: the default secret (the OpenAI key) sent to whatever host the leaf names
    from mas.runtime.registry.llm_provider_registry import instantiate_llm_provider
    from mas.runtime.secrets.ref import SecretRef

    assert instantiate_llm_provider("openai", {}).api_key == SecretRef(name="openai")
    assert instantiate_llm_provider("openai", {"api_base": "https://api.openai.com/v1/"}).api_key == SecretRef(
        name="openai"
    )
    for other in ("http://127.0.0.1:9/v1", "https://api.openai.com.evil.test/v1", "http://api.openai.com/v1"):
        assert instantiate_llm_provider("openai", {"api_base": other}).api_key is None, other
    assert OpenAILLMProvider.from_infra_spec({"api_base": "https://gw.example/v1"}).api_key is None


def test_openai_from_infra_spec_reads_proxy_and_protocol_fields():
    provider = OpenAILLMProvider.from_infra_spec(
        {
            "protocol": "openai",
            "api_base": "https://example.test/v1",
            "stream": True,
            "reasoning_effort": "low",
            "timeout": 7,
        }
    )
    assert provider.api_base == "https://example.test/v1"
    assert provider.api_key is None
    assert provider.stream is True
    assert provider.reasoning_effort == "low"
    assert provider.http_timeout == 7.0
    assert provider._timeout() == 7.0


async def test_cache_provider_write_only_does_not_read(tmp_path) -> None:
    cache_path = tmp_path / "llm-cache.json"
    inner = _StubInner({"role": "assistant", "content": "fresh-response"})
    writer = CacheLLMProvider(inner, cache_path=cache_path, allow_read=False, allow_write=True)
    out = await writer.achat_completion(model="m", messages=[{"role": "user", "content": "hi"}])
    assert out["content"] == "fresh-response"
    assert cache_path.is_file()
    assert inner.calls == 1

    inner2 = _StubInner({"role": "assistant", "content": "should-not-use-cache"})
    reader = CacheLLMProvider(inner2, cache_path=cache_path, allow_read=False, allow_write=True)
    out2 = await reader.achat_completion(model="m", messages=[{"role": "user", "content": "hi"}])
    assert out2["content"] == "should-not-use-cache"
    assert inner2.calls == 1


async def test_cache_provider_read_hit_skips_inner(tmp_path) -> None:
    cache_path = tmp_path / "llm-cache.json"
    writer = CacheLLMProvider(
        _StubInner({"role": "assistant", "content": "cached-response"}),
        cache_path=cache_path,
        allow_read=False,
        allow_write=True,
    )
    await writer.achat_completion(model="m", messages=[{"role": "user", "content": "hello"}])

    inner = _StubInner({"role": "assistant", "content": "live"})
    reader = CacheLLMProvider(inner, cache_path=cache_path, allow_read=True, allow_write=False)
    out = await reader.achat_completion(model="m", messages=[{"role": "user", "content": "hello"}])
    assert out["content"] == "cached-response"
    assert inner.calls == 0


async def test_cache_provider_persists_tool_calls(tmp_path) -> None:
    cache_path = tmp_path / "llm-cache.json"
    tool_calls = [{"id": "call_1", "type": "function", "function": {"name": "demo_tool", "arguments": "{}"}}]
    writer = CacheLLMProvider(
        _StubInner({"role": "assistant", "content": None, "tool_calls": tool_calls, "usage": {"total_tokens": 12}}),
        cache_path=cache_path,
        allow_read=False,
        allow_write=True,
    )
    await writer.achat_completion(model="m", messages=[{"role": "user", "content": "hi"}], tools=[{"type": "function"}])

    inner = _StubInner({"role": "assistant", "content": "miss"})
    reader = CacheLLMProvider(inner, cache_path=cache_path, allow_read=True, allow_write=False)
    out = await reader.achat_completion(
        model="m", messages=[{"role": "user", "content": "hi"}], tools=[{"type": "function"}]
    )
    assert out["tool_calls"][0]["function"]["name"] == "demo_tool"
    assert inner.calls == 0


def test_llm_provider_from_infra_wraps_cache(tmp_path) -> None:
    from mas.library.standard.plugins.infra.llm_cache import LlmCacheMiddleware
    from mas.runtime.engine.infra_pipeline import PipedLLMProvider

    provider = llm_provider_from_infra(
        {"protocol": "openai", "api_base": "https://api.openai.com/v1"},
        wrap_cache=True,
        cache_path=tmp_path / "c.json",
        cache_read=True,
        cache_write=True,
    )
    assert isinstance(provider, PipedLLMProvider)
    cur = getattr(provider, "_stack", provider)
    cache = None
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, LlmCacheMiddleware):
            cache = cur
            break
        cur = getattr(cur, "inner", None)
    assert cache is not None
    assert isinstance(_protocol(provider), OpenAILLMProvider)


class _FakeStreamResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    is_error = False

    def raise_for_status(self) -> None:
        return None

    async def aread(self) -> bytes:
        return b""

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def __aenter__(self) -> "_FakeStreamResponse":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeClient:
    def __init__(self, lines: list[str], **_: object) -> None:
        self._lines = lines

    def stream(self, method: str, url: str, *, json: object, headers: object, params: object = None):
        assert method == "POST"
        return _FakeStreamResponse(self._lines)

    async def aclose(self) -> None:
        return None

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


def _sse(chunk: dict) -> str:
    return f"data: {json.dumps(chunk)}"


async def test_openai_provider_streams_content_chunks(monkeypatch) -> None:
    lines = [
        _sse({"choices": [{"delta": {"content": "Hel"}}]}),
        _sse({"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: _FakeClient(lines, **kwargs))
    received: list[str] = []
    provider = OpenAILLMProvider(stream=True)
    message = await provider.achat_completion(
        model="gpt",
        messages=[{"role": "user", "content": "hi"}],
        on_stream_chunk=received.append,
    )
    assert received == ["Hel", "lo"]
    assert message["content"] == "Hello"
    assert message["finish_reason"] == "stop"


async def test_openai_provider_stream_skips_noise_and_accumulates_tool_calls(monkeypatch) -> None:
    def _boom(text: str) -> None:
        if text == "x":
            raise RuntimeError("callback")

    lines = [
        "event: ping",
        b"data: not-json",
        "data: ",
        _sse({"usage": {"prompt_tokens": 1}, "choices": []}),
        _sse({"choices": [{"delta": {"content": "x"}}]}),
        _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [{"index": 0, "id": "c1", "function": {"name": "lookup", "arguments": "{"}}]
                        }
                    }
                ]
            }
        ),
        _sse(
            {
                "choices": [
                    {
                        "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "}"}}]},
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        ),
        "data: [DONE]",
    ]
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: _FakeClient(lines, **kwargs))
    message = await OpenAILLMProvider(stream=True).achat_completion(
        model="gpt",
        messages=[{"role": "user", "content": "hi"}],
        on_stream_chunk=_boom,
    )
    assert message["content"] == "x"
    assert message["usage"] == {"prompt_tokens": 1}
    assert message["finish_reason"] == "tool_calls"
    assert message["tool_calls"] == [
        {"id": "c1", "type": "function", "function": {"name": "lookup", "arguments": "{}"}}
    ]


class _FakePostResponse:
    def __init__(self, message: dict) -> None:
        self._message = message
        self.status_code = 200
        self.request = None

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": dict(self._message), "finish_reason": "stop"}]}


class _FakePostClient:
    last_json: dict | None = None

    def __init__(self, message: dict, **_: object) -> None:
        self._message = message

    async def post(self, url: str, json: object, headers: object, params: object = None):
        _FakePostClient.last_json = json if isinstance(json, dict) else None
        return _FakePostResponse(self._message)

    async def request(self, method: str, url: str, **kwargs):
        return await self.post(
            url,
            json=kwargs.get("json"),
            headers=kwargs.get("headers"),
            params=kwargs.get("params"),
        )

    async def aclose(self) -> None:
        return None

    async def __aenter__(self) -> "_FakePostClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


def test_openai_from_manifest_binds_nested_reasoning():
    provider = llm_providers_from_manifest(
        {
            "spec": {
                "models": [
                    {
                        "model": "gpt-5",
                        "kind": "openai",
                        "reasoning": {"effort": "high", "budget_tokens": 64, "exclude": True},
                    }
                ]
            }
        },
        llm_proxy={"api_base": "https://example.test/v1"},
    )
    provider = _protocol(provider)
    assert isinstance(provider, OpenAILLMProvider)
    assert provider.reasoning.effort == "high"
    assert provider.reasoning.budget_tokens == 64
    assert provider.reasoning.exclude_from_spec is True


async def test_openai_provider_sends_reasoning_payload_and_strips_cot(monkeypatch) -> None:
    message = {
        "role": "assistant",
        "content": "<think>plan</think>Answer",
        "reasoning_content": "secret",
    }
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: _FakePostClient(message, **kwargs))
    provider = OpenAILLMProvider(
        reasoning={"effort": "low", "budget_tokens": 32, "exclude": True},
    )
    out = await provider.achat_completion(
        model="gpt-5",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=900,
    )
    payload = _FakePostClient.last_json
    assert payload is not None
    assert payload["reasoning_effort"] == "low"
    assert "reasoning" not in payload
    assert payload["max_completion_tokens"] == 900
    assert "max_tokens" not in payload
    assert out["content"] == "Answer"
    assert "reasoning_content" not in out


async def test_openai_provider_sends_mode_think_include(monkeypatch) -> None:
    message = {"role": "assistant", "content": "ok"}
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: _FakePostClient(message, **kwargs))
    provider = OpenAILLMProvider(
        reasoning={"effort": "low", "mode": "pro", "think": True, "include": ["reasoning.encrypted_content"]},
    )
    await provider.achat_completion(model="gpt-5", messages=[{"role": "user", "content": "hi"}])
    payload = _FakePostClient.last_json
    assert payload is not None
    assert "think" not in payload
    assert payload["include"] == ["reasoning.encrypted_content"]
    assert payload["reasoning"]["mode"] == "pro"


async def test_openai_provider_flattens_vllm_think_into_json_body(monkeypatch) -> None:
    message = {"role": "assistant", "content": "ok"}
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: _FakePostClient(message, **kwargs))
    provider = OpenAILLMProvider(reasoning={"think": True, "exclude": True})
    await provider.achat_completion(model="onprem/gemma4", messages=[{"role": "user", "content": "hi"}])
    payload = _FakePostClient.last_json
    assert payload is not None
    assert "think" not in payload
    assert "extra_body" not in payload
    assert payload["chat_template_kwargs"]["enable_thinking"] is True
    assert "max_tokens" not in payload
    assert "max_completion_tokens" not in payload
    assert "max_completion_tokens" not in payload


async def test_openai_provider_stream_drops_think_tags(monkeypatch) -> None:
    lines = [
        _sse({"choices": [{"delta": {"content": "<think>nope</think>Hel"}}]}),
        _sse({"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: _FakeClient(lines, **kwargs))
    received: list[str] = []
    provider = OpenAILLMProvider(stream=True, reasoning={"exclude": True})
    message = await provider.achat_completion(
        model="gpt",
        messages=[{"role": "user", "content": "hi"}],
        on_stream_chunk=received.append,
    )
    assert "".join(received) == "Hello"
    assert message["content"] == "Hello"


async def test_openai_provider_default_exclude_strips_without_sending_extension(monkeypatch) -> None:
    message = {
        "role": "assistant",
        "content": "<think>plan</think>Answer",
        "reasoning_content": "secret",
    }
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: _FakePostClient(message, **kwargs))
    out = await OpenAILLMProvider().achat_completion(
        model="gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=100,
    )
    payload = _FakePostClient.last_json
    assert payload is not None
    assert "reasoning" not in payload
    assert "reasoning_effort" not in payload
    assert payload["max_tokens"] == 100
    assert "max_completion_tokens" not in payload
    assert out["content"] == "Answer"
    assert "reasoning_content" not in out


class _FlakyPostClient:
    """Raises a transport error the first `fail_times` calls, then succeeds."""

    def __init__(self, message: dict, *, fail_times: int = 0, exc_factory=None, **_: object) -> None:
        self._message = message
        self._fail_times = fail_times
        self._exc_factory = exc_factory or (lambda: httpx.ConnectError("boom"))
        self.calls = 0

    async def post(self, url: str, json: object, headers: object, params: object = None):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise self._exc_factory()
        return _FakePostResponse(self._message)

    async def request(self, method: str, url: str, **kwargs):
        return await self.post(
            url,
            json=kwargs.get("json"),
            headers=kwargs.get("headers"),
            params=kwargs.get("params"),
        )

    async def aclose(self) -> None:
        return None

    async def __aenter__(self) -> "_FlakyPostClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


def _silence_retry_sleep(monkeypatch) -> None:
    monkeypatch.setattr("mas.library.standard.lib.retry.loop.time.sleep", lambda _s: None)

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr("mas.library.standard.lib.retry.loop.asyncio.sleep", _no_sleep)


async def test_openai_provider_retries_dropped_connection_then_succeeds(monkeypatch) -> None:
    _silence_retry_sleep(monkeypatch)
    client = _FlakyPostClient({"role": "assistant", "content": "ok"}, fail_times=2)
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: client)
    out = await _retrying(OpenAILLMProvider()).achat_completion(
        model="gpt", messages=[{"role": "user", "content": "hi"}]
    )
    assert out["content"] == "ok"
    assert client.calls == 3


async def test_openai_provider_gives_up_after_max_attempts(monkeypatch) -> None:
    _silence_retry_sleep(monkeypatch)
    client = _FlakyPostClient({"role": "assistant", "content": "ok"}, fail_times=10)
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: client)
    with pytest.raises(RuntimeError):
        await _retrying(OpenAILLMProvider()).achat_completion(model="gpt", messages=[{"role": "user", "content": "hi"}])
    assert client.calls == 4


async def test_openai_provider_recreates_client_after_transport_failure(monkeypatch) -> None:
    """A network change can wedge the pooled client; retry must use a fresh one."""
    _silence_retry_sleep(monkeypatch)
    created: list[_FlakyPostClient] = []

    def factory(**kwargs):
        fail_times = 10 if not created else 0
        client = _FlakyPostClient({"role": "assistant", "content": "ok"}, fail_times=fail_times)
        created.append(client)
        return client

    monkeypatch.setattr("httpx.AsyncClient", factory)
    out = await _retrying(OpenAILLMProvider()).achat_completion(
        model="gpt", messages=[{"role": "user", "content": "hi"}]
    )
    assert out["content"] == "ok"
    assert len(created) == 2
    assert created[0].calls == 1
    assert created[1].calls == 1


async def test_openai_provider_keeps_client_after_http_429(monkeypatch) -> None:
    _silence_retry_sleep(monkeypatch)

    class _StatusThenOkClient:
        def __init__(self) -> None:
            self.calls = 0
            self.closed = 0

        async def request(self, method: str, url: str, **kwargs):
            self.calls += 1
            if self.calls == 1:
                req = httpx.Request(method, url)
                return httpx.Response(429, request=req)
            return _FakePostResponse({"role": "assistant", "content": "ok"})

        async def aclose(self) -> None:
            self.closed += 1

    client = _StatusThenOkClient()
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: client)
    out = await _retrying(OpenAILLMProvider()).achat_completion(
        model="gpt", messages=[{"role": "user", "content": "hi"}]
    )
    assert out["content"] == "ok"
    assert client.calls == 2
    assert client.closed == 0


async def test_openai_provider_does_not_retry_auth_error(monkeypatch) -> None:
    _silence_retry_sleep(monkeypatch)

    def _auth_error() -> httpx.HTTPStatusError:
        request = httpx.Request("POST", "https://example.test/v1/chat/completions")
        response = httpx.Response(401, request=request)
        return httpx.HTTPStatusError("unauthorized", request=request, response=response)

    client = _FlakyPostClient({"role": "assistant", "content": "ok"}, fail_times=1, exc_factory=_auth_error)
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: client)
    with pytest.raises(RuntimeError):
        await _retrying(OpenAILLMProvider()).achat_completion(model="gpt", messages=[{"role": "user", "content": "hi"}])
    assert client.calls == 1


class _FlakyStreamClient:
    """`.stream()` raises a transport error the first `fail_times` calls."""

    def __init__(self, lines: list[str], *, fail_times: int = 0, **_: object) -> None:
        self._lines = lines
        self._fail_times = fail_times
        self.calls = 0

    def stream(self, method: str, url: str, *, json: object, headers: object, params: object = None):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise httpx.ConnectError("boom")
        return _FakeStreamResponse(self._lines)

    async def aclose(self) -> None:
        return None

    async def __aenter__(self) -> "_FlakyStreamClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


async def test_openai_provider_retries_stream_connect_failure(monkeypatch) -> None:
    _silence_retry_sleep(monkeypatch)
    lines = [
        _sse({"choices": [{"delta": {"content": "Hi"}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]
    client = _FlakyStreamClient(lines, fail_times=2)
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: client)
    received: list[str] = []
    provider = _retrying(OpenAILLMProvider(stream=True))
    message = await provider.achat_completion(
        model="gpt",
        messages=[{"role": "user", "content": "hi"}],
        on_stream_chunk=received.append,
    )
    assert received == ["Hi"]
    assert message["content"] == "Hi"
    assert client.calls == 3


class _DroppedMidStreamResponse:
    is_error = False

    async def aread(self) -> bytes:
        return b""

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line
        raise httpx.RemoteProtocolError("peer closed connection")

    async def __aenter__(self) -> "_DroppedMidStreamResponse":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _MidStreamDropClient:
    def __init__(self, lines: list[str], **_: object) -> None:
        self._lines = lines
        self.calls = 0

    def stream(self, method: str, url: str, *, json: object, headers: object, params: object = None):
        self.calls += 1
        return _DroppedMidStreamResponse(self._lines)

    async def aclose(self) -> None:
        return None

    async def __aenter__(self) -> "_MidStreamDropClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


async def test_openai_provider_does_not_retry_after_partial_stream_emitted(monkeypatch) -> None:
    from mas.runtime.reliability.classes import ClassifiedFailure, FailureClass

    _silence_retry_sleep(monkeypatch)
    lines = [_sse({"choices": [{"delta": {"content": "Hel"}}]})]
    client = _MidStreamDropClient(lines)
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: client)
    received: list[str] = []
    provider = OpenAILLMProvider(stream=True)
    with pytest.raises(ClassifiedFailure) as caught:
        await provider.achat_completion(
            model="gpt",
            messages=[{"role": "user", "content": "hi"}],
            on_stream_chunk=received.append,
        )
    assert received == ["Hel"]
    assert client.calls == 1
    assert caught.value.failure_class is FailureClass.APPLICATION
    assert caught.value.code == "STREAM_PARTIAL"


async def test_openai_provider_stream_ignores_reasoning_deltas(monkeypatch) -> None:
    lines = [
        _sse({"choices": [{"delta": {"reasoning_content": "secret", "content": "Hi"}}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: _FakeClient(lines, **kwargs))
    received: list[str] = []
    provider = OpenAILLMProvider(stream=True)
    message = await provider.achat_completion(
        model="gpt",
        messages=[{"role": "user", "content": "hi"}],
        on_stream_chunk=received.append,
    )
    assert received == ["Hi"]
    assert message["content"] == "Hi"


@pytest.mark.asyncio
async def test_openai_achat_completion_uses_shared_request_builder(monkeypatch) -> None:
    message = {
        "role": "assistant",
        "content": "<think>plan</think>Answer",
        "reasoning_content": "secret",
    }

    class _AsyncPostClient:
        last_json: dict | None = None

        def __init__(self, **_: object) -> None:
            return None

        async def post(self, url: str, json: object, headers: object, params: object = None):
            _AsyncPostClient.last_json = json if isinstance(json, dict) else None
            return _FakePostResponse(message)

        async def request(self, method: str, url: str, **kwargs):
            return await self.post(
                url,
                json=kwargs.get("json"),
                headers=kwargs.get("headers"),
                params=kwargs.get("params"),
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: _AsyncPostClient(**kwargs))
    provider = OpenAILLMProvider(reasoning={"effort": "low", "budget_tokens": 32, "exclude": True})
    out = await provider.achat_completion(
        model="gpt-5",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=900,
    )
    payload = _AsyncPostClient.last_json
    assert payload is not None
    assert payload["reasoning_effort"] == "low"
    assert payload["max_completion_tokens"] == 900
    assert out["content"] == "Answer"


@pytest.mark.asyncio
async def test_openai_provider_recreates_async_client_after_transport_failure(monkeypatch) -> None:
    _silence_retry_sleep(monkeypatch)
    created: list[_FlakyAsyncPostClient] = []

    class _FlakyAsyncPostClient:
        def __init__(self, *, fail_times: int) -> None:
            self._fail_times = fail_times
            self.calls = 0

        async def request(self, method: str, url: str, **kwargs):
            self.calls += 1
            if self.calls <= self._fail_times:
                raise httpx.ConnectError("boom")
            return _FakePostResponse({"role": "assistant", "content": "ok"})

        async def aclose(self) -> None:
            return None

    def factory(**kwargs):
        client = _FlakyAsyncPostClient(fail_times=10 if not created else 0)
        created.append(client)
        return client

    monkeypatch.setattr("httpx.AsyncClient", factory)
    out = await _retrying(OpenAILLMProvider()).achat_completion(
        model="gpt", messages=[{"role": "user", "content": "hi"}]
    )
    assert out["content"] == "ok"
    assert len(created) == 2
    assert created[0].calls == 1
    assert created[1].calls == 1


@pytest.mark.asyncio
async def test_openai_provider_does_not_retry_async_after_partial_stream(monkeypatch) -> None:
    from mas.runtime.reliability.classes import ClassifiedFailure, FailureClass

    _silence_retry_sleep(monkeypatch)

    class _DroppedMidStreamAsyncResponse:
        is_error = False

        async def aread(self) -> bytes:
            return b""

        def __init__(self, lines: list[str]) -> None:
            self._lines = lines

        def raise_for_status(self) -> None:
            return None

        async def aiter_lines(self):
            for line in self._lines:
                yield line
            raise httpx.RemoteProtocolError("peer closed connection")

        async def __aenter__(self) -> "_DroppedMidStreamAsyncResponse":
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

    class _MidStreamDropAsyncClient:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, method: str, url: str, *, json: object, headers: object, params: object = None):
            self.calls += 1
            return _DroppedMidStreamAsyncResponse([_sse({"choices": [{"delta": {"content": "Hel"}}]})])

        async def aclose(self) -> None:
            return None

    client = _MidStreamDropAsyncClient()
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: client)
    received: list[str] = []
    with pytest.raises(ClassifiedFailure) as caught:
        await OpenAILLMProvider(stream=True).achat_completion(
            model="gpt",
            messages=[{"role": "user", "content": "hi"}],
            on_stream_chunk=received.append,
        )
    assert received == ["Hel"]
    assert client.calls == 1
    assert caught.value.failure_class is FailureClass.APPLICATION
    assert caught.value.code == "STREAM_PARTIAL"


@pytest.mark.asyncio
async def test_openai_provider_keeps_async_client_after_http_429(monkeypatch) -> None:
    _silence_retry_sleep(monkeypatch)

    class _StatusThenOkAsyncClient:
        def __init__(self) -> None:
            self.calls = 0
            self.closed = 0

        async def request(self, method: str, url: str, **kwargs):
            self.calls += 1
            if self.calls == 1:
                req = httpx.Request(method, url)
                return httpx.Response(429, request=req)
            return _FakePostResponse({"role": "assistant", "content": "ok"})

        async def aclose(self) -> None:
            self.closed += 1

    client = _StatusThenOkAsyncClient()
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: client)
    out = await _retrying(OpenAILLMProvider()).achat_completion(
        model="gpt", messages=[{"role": "user", "content": "hi"}]
    )
    assert out["content"] == "ok"
    assert client.calls == 2
    assert client.closed == 0


class _FakeAsyncStreamResponse:
    is_error = False

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def raise_for_status(self) -> None:
        return None

    async def aread(self) -> bytes:
        return b""

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def __aenter__(self) -> "_FakeAsyncStreamResponse":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


@pytest.mark.asyncio
async def test_openai_provider_retries_async_stream_connect_failure(monkeypatch) -> None:
    _silence_retry_sleep(monkeypatch)
    lines = [
        _sse({"choices": [{"delta": {"content": "Hi"}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]

    class _FlakyAsyncStreamClient:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, method: str, url: str, *, json: object, headers: object, params: object = None):
            self.calls += 1
            if self.calls <= 2:
                raise httpx.ConnectError("boom")
            return _FakeAsyncStreamResponse(lines)

        async def aclose(self) -> None:
            return None

    client = _FlakyAsyncStreamClient()
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: client)
    received: list[str] = []
    message = await _retrying(OpenAILLMProvider(stream=True)).achat_completion(
        model="gpt",
        messages=[{"role": "user", "content": "hi"}],
        on_stream_chunk=received.append,
    )
    assert received == ["Hi"]
    assert message["content"] == "Hi"
    assert client.calls == 3


@pytest.mark.asyncio
async def test_openai_provider_async_stream_records_usage(monkeypatch) -> None:
    lines = [
        _sse({"usage": {"prompt_tokens": 2}, "choices": [{"delta": {"content": "Hi"}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]

    class _AsyncStreamClient:
        def stream(self, method: str, url: str, *, json: object, headers: object, params: object = None):
            return _FakeAsyncStreamResponse(lines)

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: _AsyncStreamClient())
    message = await OpenAILLMProvider(stream=True).achat_completion(
        model="gpt", messages=[{"role": "user", "content": "hi"}]
    )
    assert message["content"] == "Hi"
    assert message["usage"] == {"prompt_tokens": 2}
    assert message["finish_reason"] == "stop"


def test_resolve_ssl_verify_falls_back_to_httpx_default(monkeypatch) -> None:
    from mas.library.standard.lib import tls as llm_http

    monkeypatch.setattr(llm_http, "_system_ssl_context", lambda: None)
    monkeypatch.setattr(llm_http, "_default_ca_bundle", lambda: None)
    monkeypatch.delenv("MAS_LLM_VERIFY_SSL", raising=False)
    assert llm_http.resolve_ssl_verify() is True


@pytest.mark.asyncio
async def test_openai_provider_async_stream_skips_noise_and_accumulates_tool_calls(monkeypatch) -> None:
    def _boom(text: str) -> None:
        if text == "x":
            raise RuntimeError("callback")

    lines = [
        "event: ping",
        b"data: not-json",
        "data: ",
        _sse({"usage": {"prompt_tokens": 1}, "choices": []}),
        _sse({"choices": [{"delta": {"content": "x"}}]}),
        _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [{"index": 0, "id": "c1", "function": {"name": "lookup", "arguments": "{"}}]
                        }
                    }
                ]
            }
        ),
        _sse(
            {
                "choices": [
                    {
                        "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "}"}}]},
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        ),
        "data: [DONE]",
    ]

    class _AsyncStreamClient:
        def stream(self, method: str, url: str, *, json: object, headers: object, params: object = None):
            return _FakeAsyncStreamResponse(lines)

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: _AsyncStreamClient())
    message = await OpenAILLMProvider(stream=True).achat_completion(
        model="gpt",
        messages=[{"role": "user", "content": "hi"}],
        on_stream_chunk=_boom,
    )
    assert message["content"] == "x"
    assert message["usage"] == {"prompt_tokens": 1}
    assert message["finish_reason"] == "tool_calls"
    assert message["tool_calls"] == [
        {"id": "c1", "type": "function", "function": {"name": "lookup", "arguments": "{}"}}
    ]


@pytest.mark.asyncio
async def test_cache_provider_achat_completion_parity(tmp_path) -> None:
    inner = _StubInner({"role": "assistant", "content": "cached-async"})
    writer = CacheLLMProvider(inner, cache_path=tmp_path / "c.json", allow_read=False, allow_write=True)
    await writer.achat_completion(model="m", messages=[{"role": "user", "content": "hi"}])
    reader_inner = _StubInner({"role": "assistant", "content": "live"})
    reader = CacheLLMProvider(reader_inner, cache_path=tmp_path / "c.json", allow_read=True, allow_write=False)
    out = await reader.achat_completion(model="m", messages=[{"role": "user", "content": "hi"}])
    assert out["content"] == "cached-async"
    assert reader_inner.calls == 0
