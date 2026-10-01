#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""LLMProvider plugins — openai protocol, cache wrapper, registry lookup."""

from __future__ import annotations

import json

import httpx
import pytest
from mas.library.standard.plugins.llm.cache import CacheLLMProvider
from mas.library.standard.plugins.llm.openai import OpenAILLMProvider
from mas.runtime.registry.llm_provider_registry import (
    LLMProviderRegistry,
    iter_model_provider_specs,
    llm_provider_class,
    llm_provider_from_infra,
    llm_provider_kind,
    llm_providers_from_manifest,
    protocol_kind_from_infra,
)


class _StubInner:
    def __init__(self, message: dict, *, kind: str = "openai") -> None:
        self.message = message
        self.kind = kind
        self.calls = 0

    def chat_completion(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        return dict(self.message)

    async def achat_completion(self, **kwargs):
        return self.chat_completion(**kwargs)


def test_require_achat_completion_rejects_sync_only_provider():
    from mas.runtime.registry.llm_provider_protocol import (
        MissingLLMProviderAsyncContract,
        require_achat_completion,
    )

    class SyncOnly:
        def chat_completion(self, **kwargs):
            return {}

    with pytest.raises(MissingLLMProviderAsyncContract, match="achat_completion"):
        require_achat_completion(SyncOnly, name="sync-only")

    registry = LLMProviderRegistry()
    with pytest.raises(MissingLLMProviderAsyncContract):
        registry.register_provider(SyncOnly())


def test_llm_provider_kind_defaults_to_openai():
    assert llm_provider_kind({}) == "openai"
    assert llm_provider_kind({"protocol": "bedrock"}) == "bedrock"
    assert llm_provider_kind({"protocol": "mock"}) == "openai"
    assert protocol_kind_from_infra({"protocol": "mock"}) == "openai"
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


def test_iter_model_specs_rejects_cache_and_mock_as_protocol():
    with pytest.raises(ValueError, match="not a wire protocol"):
        iter_model_provider_specs({"spec": {"models": [{"model": "m", "kind": "cache"}]}})
    with pytest.raises(ValueError, match="not a wire protocol"):
        iter_model_provider_specs({"spec": {"models": [{"model": "m", "kind": "mock"}]}})


def test_registry_routes_model_names_like_tools():
    openai = _StubInner({"role": "assistant", "content": "oai"}, kind="openai")
    bedrock = _StubInner({"role": "assistant", "content": "br"}, kind="bedrock")
    registry = LLMProviderRegistry()
    registry.register_provider(openai, models=["gpt-4o-mini"])
    registry.register_provider(bedrock, models=["claude-sonnet"])
    registry.initialize()
    assert registry.provider_for("gpt-4o-mini") is openai
    assert registry.provider_for("claude-sonnet") is bedrock
    assert registry.chat_completion(model="claude-sonnet", messages=[])["content"] == "br"


def test_llm_providers_from_manifest_uses_models_kind():
    provider = llm_providers_from_manifest(
        {"spec": {"models": [{"model": "gpt-4o-mini", "kind": "openai"}]}},
        llm_proxy={"api_base": "https://example.test/v1", "api_key_env": "CUSTOM_KEY"},
    )
    assert isinstance(provider, OpenAILLMProvider)
    assert provider.api_base == "https://example.test/v1"
    assert provider.api_key_env == "CUSTOM_KEY"


def test_registry_resolves_openai_and_cache():
    assert llm_provider_class("openai") is OpenAILLMProvider
    assert llm_provider_class("cache") is CacheLLMProvider


def test_openai_from_infra_spec_reads_proxy_and_protocol_fields():
    provider = OpenAILLMProvider.from_infra_spec(
        {
            "protocol": "openai",
            "api_base": "https://example.test/v1",
            "api_key_env": "CUSTOM_KEY",
            "stream": True,
            "reasoning_effort": "low",
            "timeout": 7,
        }
    )
    assert provider.api_base == "https://example.test/v1"
    assert provider.api_key_env == "CUSTOM_KEY"
    assert provider.stream is True
    assert provider.reasoning_effort == "low"
    assert provider.http_timeout == 7.0
    assert provider._timeout() == 7.0


def test_cache_provider_write_only_does_not_read(tmp_path) -> None:
    cache_path = tmp_path / "llm-cache.json"
    inner = _StubInner({"role": "assistant", "content": "fresh-response"})
    writer = CacheLLMProvider(inner, cache_path=cache_path, allow_read=False, allow_write=True)
    out = writer.chat_completion(model="m", messages=[{"role": "user", "content": "hi"}])
    assert out["content"] == "fresh-response"
    assert cache_path.is_file()
    assert inner.calls == 1

    inner2 = _StubInner({"role": "assistant", "content": "should-not-use-cache"})
    reader = CacheLLMProvider(inner2, cache_path=cache_path, allow_read=False, allow_write=True)
    out2 = reader.chat_completion(model="m", messages=[{"role": "user", "content": "hi"}])
    assert out2["content"] == "should-not-use-cache"
    assert inner2.calls == 1


def test_cache_provider_read_hit_skips_inner(tmp_path) -> None:
    cache_path = tmp_path / "llm-cache.json"
    writer = CacheLLMProvider(
        _StubInner({"role": "assistant", "content": "cached-response"}),
        cache_path=cache_path,
        allow_read=False,
        allow_write=True,
    )
    writer.chat_completion(model="m", messages=[{"role": "user", "content": "hello"}])

    inner = _StubInner({"role": "assistant", "content": "live"})
    reader = CacheLLMProvider(inner, cache_path=cache_path, allow_read=True, allow_write=False)
    out = reader.chat_completion(model="m", messages=[{"role": "user", "content": "hello"}])
    assert out["content"] == "cached-response"
    assert inner.calls == 0


def test_cache_provider_persists_tool_calls(tmp_path) -> None:
    cache_path = tmp_path / "llm-cache.json"
    tool_calls = [
        {"id": "call_1", "type": "function", "function": {"name": "demo_tool", "arguments": "{}"}}
    ]
    writer = CacheLLMProvider(
        _StubInner({"role": "assistant", "content": None, "tool_calls": tool_calls, "usage": {"total_tokens": 12}}),
        cache_path=cache_path,
        allow_read=False,
        allow_write=True,
    )
    writer.chat_completion(model="m", messages=[{"role": "user", "content": "hi"}], tools=[{"type": "function"}])

    inner = _StubInner({"role": "assistant", "content": "miss"})
    reader = CacheLLMProvider(inner, cache_path=cache_path, allow_read=True, allow_write=False)
    out = reader.chat_completion(model="m", messages=[{"role": "user", "content": "hi"}], tools=[{"type": "function"}])
    assert out["tool_calls"][0]["function"]["name"] == "demo_tool"
    assert inner.calls == 0


def test_llm_provider_from_infra_wraps_cache(tmp_path) -> None:
    provider = llm_provider_from_infra(
        {"protocol": "openai", "api_base": "https://api.openai.com/v1"},
        wrap_cache=True,
        cache_path=tmp_path / "c.json",
        cache_read=True,
        cache_write=True,
    )
    assert isinstance(provider, CacheLLMProvider)
    assert isinstance(provider.inner, OpenAILLMProvider)


class _FakeStreamResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self):
        return iter(self._lines)

    def __enter__(self) -> "_FakeStreamResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _FakeClient:
    def __init__(self, lines: list[str], **_: object) -> None:
        self._lines = lines

    def stream(self, method: str, url: str, *, json: object, headers: object, params: object = None):
        assert method == "POST"
        return _FakeStreamResponse(self._lines)

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _sse(chunk: dict) -> str:
    return f"data: {json.dumps(chunk)}"


def test_openai_provider_streams_content_chunks(monkeypatch) -> None:
    lines = [
        _sse({"choices": [{"delta": {"content": "Hel"}}]}),
        _sse({"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]
    monkeypatch.setattr("httpx.Client", lambda **kwargs: _FakeClient(lines, **kwargs))
    received: list[str] = []
    provider = OpenAILLMProvider(stream=True)
    message = provider.chat_completion(
        model="gpt",
        messages=[{"role": "user", "content": "hi"}],
        api_key="k",
        on_stream_chunk=received.append,
    )
    assert received == ["Hel", "lo"]
    assert message["content"] == "Hello"
    assert message["finish_reason"] == "stop"


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

    def post(self, url: str, json: object, headers: object, params: object = None):
        _FakePostClient.last_json = json if isinstance(json, dict) else None
        return _FakePostResponse(self._message)

    def request(self, method: str, url: str, **kwargs):
        return self.post(
            url,
            json=kwargs.get("json"),
            headers=kwargs.get("headers"),
            params=kwargs.get("params"),
        )

    def __enter__(self) -> "_FakePostClient":
        return self

    def __exit__(self, *exc: object) -> None:
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
        llm_proxy={"api_base": "https://example.test/v1", "api_key_env": "CUSTOM_KEY"},
    )
    assert isinstance(provider, OpenAILLMProvider)
    assert provider.reasoning.effort == "high"
    assert provider.reasoning.budget_tokens == 64
    assert provider.reasoning.exclude_from_spec is True


def test_openai_provider_sends_reasoning_payload_and_strips_cot(monkeypatch) -> None:
    message = {
        "role": "assistant",
        "content": "<think>plan</think>Answer",
        "reasoning_content": "secret",
    }
    monkeypatch.setattr("httpx.Client", lambda **kwargs: _FakePostClient(message, **kwargs))
    provider = OpenAILLMProvider(
        reasoning={"effort": "low", "budget_tokens": 32, "exclude": True},
    )
    out = provider.chat_completion(
        model="gpt-5",
        messages=[{"role": "user", "content": "hi"}],
        api_key="k",
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


def test_openai_provider_sends_mode_think_include(monkeypatch) -> None:
    message = {"role": "assistant", "content": "ok"}
    monkeypatch.setattr("httpx.Client", lambda **kwargs: _FakePostClient(message, **kwargs))
    provider = OpenAILLMProvider(
        reasoning={"effort": "low", "mode": "pro", "think": True, "include": ["reasoning.encrypted_content"]},
    )
    provider.chat_completion(model="gpt-5", messages=[{"role": "user", "content": "hi"}], api_key="k")
    payload = _FakePostClient.last_json
    assert payload is not None
    assert "think" not in payload
    assert payload["include"] == ["reasoning.encrypted_content"]
    assert payload["reasoning"]["mode"] == "pro"


def test_openai_provider_flattens_vllm_think_into_json_body(monkeypatch) -> None:
    message = {"role": "assistant", "content": "ok"}
    monkeypatch.setattr("httpx.Client", lambda **kwargs: _FakePostClient(message, **kwargs))
    provider = OpenAILLMProvider(reasoning={"think": True, "exclude": True})
    provider.chat_completion(model="onprem/gemma4", messages=[{"role": "user", "content": "hi"}], api_key="k")
    payload = _FakePostClient.last_json
    assert payload is not None
    assert "think" not in payload
    assert "extra_body" not in payload
    assert payload["chat_template_kwargs"]["enable_thinking"] is True
    assert "max_tokens" not in payload
    assert "max_completion_tokens" not in payload
    assert "max_completion_tokens" not in payload


def test_openai_provider_stream_drops_think_tags(monkeypatch) -> None:
    lines = [
        _sse({"choices": [{"delta": {"content": "<think>nope</think>Hel"}}]}),
        _sse({"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]
    monkeypatch.setattr("httpx.Client", lambda **kwargs: _FakeClient(lines, **kwargs))
    received: list[str] = []
    provider = OpenAILLMProvider(stream=True, reasoning={"exclude": True})
    message = provider.chat_completion(
        model="gpt",
        messages=[{"role": "user", "content": "hi"}],
        api_key="k",
        on_stream_chunk=received.append,
    )
    assert "".join(received) == "Hello"
    assert message["content"] == "Hello"


def test_openai_provider_default_exclude_strips_without_sending_extension(monkeypatch) -> None:
    message = {
        "role": "assistant",
        "content": "<think>plan</think>Answer",
        "reasoning_content": "secret",
    }
    monkeypatch.setattr("httpx.Client", lambda **kwargs: _FakePostClient(message, **kwargs))
    out = OpenAILLMProvider().chat_completion(
        model="gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
        api_key="k",
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

    def post(self, url: str, json: object, headers: object, params: object = None):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise self._exc_factory()
        return _FakePostResponse(self._message)

    def close(self) -> None:
        return None

    def __enter__(self) -> "_FlakyPostClient":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_openai_provider_retries_dropped_connection_then_succeeds(monkeypatch) -> None:
    monkeypatch.setattr("mas.library.standard.plugins.llm.openai.time.sleep", lambda _s: None)
    client = _FlakyPostClient({"role": "assistant", "content": "ok"}, fail_times=2)
    monkeypatch.setattr("httpx.Client", lambda **kwargs: client)
    out = OpenAILLMProvider().chat_completion(model="gpt", messages=[{"role": "user", "content": "hi"}], api_key="k")
    assert out["content"] == "ok"
    assert client.calls == 3


def test_openai_provider_gives_up_after_max_attempts(monkeypatch) -> None:
    monkeypatch.setattr("mas.library.standard.plugins.llm.openai.time.sleep", lambda _s: None)
    client = _FlakyPostClient({"role": "assistant", "content": "ok"}, fail_times=10)
    monkeypatch.setattr("httpx.Client", lambda **kwargs: client)
    with pytest.raises(RuntimeError):
        OpenAILLMProvider().chat_completion(model="gpt", messages=[{"role": "user", "content": "hi"}], api_key="k")
    assert client.calls == 3


def test_openai_provider_recreates_client_after_transport_failure(monkeypatch) -> None:
    """A network change can wedge the pooled client; retry must use a fresh one."""
    monkeypatch.setattr("mas.library.standard.plugins.llm.openai.time.sleep", lambda _s: None)
    created: list[_FlakyPostClient] = []

    def factory(**kwargs):
        fail_times = 10 if not created else 0
        client = _FlakyPostClient({"role": "assistant", "content": "ok"}, fail_times=fail_times)
        created.append(client)
        return client

    monkeypatch.setattr("httpx.Client", factory)
    out = OpenAILLMProvider().chat_completion(model="gpt", messages=[{"role": "user", "content": "hi"}], api_key="k")
    assert out["content"] == "ok"
    assert len(created) == 2
    assert created[0].calls == 1
    assert created[1].calls == 1


def test_openai_provider_does_not_retry_auth_error(monkeypatch) -> None:
    monkeypatch.setattr("mas.library.standard.plugins.llm.openai.time.sleep", lambda _s: None)

    def _auth_error() -> httpx.HTTPStatusError:
        request = httpx.Request("POST", "https://example.test/v1/chat/completions")
        response = httpx.Response(401, request=request)
        return httpx.HTTPStatusError("unauthorized", request=request, response=response)

    client = _FlakyPostClient({"role": "assistant", "content": "ok"}, fail_times=1, exc_factory=_auth_error)
    monkeypatch.setattr("httpx.Client", lambda **kwargs: client)
    with pytest.raises(RuntimeError):
        OpenAILLMProvider().chat_completion(model="gpt", messages=[{"role": "user", "content": "hi"}], api_key="k")
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

    def close(self) -> None:
        return None

    def __enter__(self) -> "_FlakyStreamClient":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_openai_provider_retries_stream_connect_failure(monkeypatch) -> None:
    monkeypatch.setattr("mas.library.standard.plugins.llm.openai.time.sleep", lambda _s: None)
    lines = [
        _sse({"choices": [{"delta": {"content": "Hi"}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]
    client = _FlakyStreamClient(lines, fail_times=2)
    monkeypatch.setattr("httpx.Client", lambda **kwargs: client)
    received: list[str] = []
    provider = OpenAILLMProvider(stream=True)
    message = provider.chat_completion(
        model="gpt",
        messages=[{"role": "user", "content": "hi"}],
        api_key="k",
        on_stream_chunk=received.append,
    )
    assert received == ["Hi"]
    assert message["content"] == "Hi"
    assert client.calls == 3


class _DroppedMidStreamResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self):
        yield from self._lines
        raise httpx.RemoteProtocolError("peer closed connection")

    def __enter__(self) -> "_DroppedMidStreamResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _MidStreamDropClient:
    def __init__(self, lines: list[str], **_: object) -> None:
        self._lines = lines

    def stream(self, method: str, url: str, *, json: object, headers: object, params: object = None):
        return _DroppedMidStreamResponse(self._lines)

    def __enter__(self) -> "_MidStreamDropClient":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_openai_provider_does_not_retry_after_partial_stream_emitted(monkeypatch) -> None:
    monkeypatch.setattr("mas.library.standard.plugins.llm.openai.time.sleep", lambda _s: None)
    lines = [_sse({"choices": [{"delta": {"content": "Hel"}}]})]
    client = _MidStreamDropClient(lines)
    monkeypatch.setattr("httpx.Client", lambda **kwargs: client)
    received: list[str] = []
    provider = OpenAILLMProvider(stream=True)
    with pytest.raises(RuntimeError):
        provider.chat_completion(
            model="gpt",
            messages=[{"role": "user", "content": "hi"}],
            api_key="k",
            on_stream_chunk=received.append,
        )
    assert received == ["Hel"]


def test_openai_provider_stream_ignores_reasoning_deltas(monkeypatch) -> None:
    lines = [
        _sse({"choices": [{"delta": {"reasoning_content": "secret", "content": "Hi"}}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]
    monkeypatch.setattr("httpx.Client", lambda **kwargs: _FakeClient(lines, **kwargs))
    received: list[str] = []
    provider = OpenAILLMProvider(stream=True)
    message = provider.chat_completion(
        model="gpt",
        messages=[{"role": "user", "content": "hi"}],
        api_key="k",
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

    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: _AsyncPostClient(**kwargs))
    provider = OpenAILLMProvider(reasoning={"effort": "low", "budget_tokens": 32, "exclude": True})
    out = await provider.achat_completion(
        model="gpt-5",
        messages=[{"role": "user", "content": "hi"}],
        api_key="k",
        max_tokens=900,
    )
    payload = _AsyncPostClient.last_json
    assert payload is not None
    assert payload["reasoning_effort"] == "low"
    assert payload["max_completion_tokens"] == 900
    assert out["content"] == "Answer"


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
