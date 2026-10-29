#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for shared LLM cache helpers and cache modes."""

from __future__ import annotations

from mas.library.standard.plugins.llm.cache import CacheLLMProvider
from mas.runtime.engine.llm_cache import (
    assistant_message_from_cache_content,
    bind_route_cache_key,
    legacy_preview_cache_key,
    llm_cache_key,
    lookup_preview_cache,
    resolve_cache_path,
)
from mas.runtime.engine.llm_live import LiveLlmEngine
from mas.runtime.schema.egress import InvokeEngineIo


def test_resolve_cache_path_prefers_explicit_argument(tmp_path, monkeypatch):
    monkeypatch.setenv("MAS_LLM_CACHE", str(tmp_path / "env-cache.json"))
    explicit = tmp_path / "explicit-cache.json"
    assert resolve_cache_path(explicit) == explicit.resolve()


def test_resolve_cache_path_falls_back_to_env_var(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    env_path = tmp_path / "env-cache.json"
    monkeypatch.setenv("MAS_LLM_CACHE", str(env_path))
    assert resolve_cache_path() == env_path.resolve()


def test_resolve_cache_path_defaults_under_shared_xdg_cache_root(tmp_path, monkeypatch):
    """Same $XDG_CACHE_HOME/mas root as the trace/artifacts caches (mas.runtime.xdg)."""
    monkeypatch.delenv("MAS_LLM_CACHE", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert resolve_cache_path() == tmp_path / "mas" / "llm_cache.json"


def test_llm_cache_key_includes_tools():
    messages = [{"role": "user", "content": "hi"}]
    without_tools = llm_cache_key("mock", messages, None)
    with_tools = llm_cache_key(
        "mock",
        messages,
        [{"type": "function", "function": {"name": "tool-a"}}],
    )
    assert without_tools != with_tools


def test_llm_cache_key_includes_sampling_and_reasoning():
    messages = [{"role": "user", "content": "hi"}]
    base = llm_cache_key("gpt-5", messages, None, params={"temperature": 0.2, "max_tokens": 10})
    hotter = llm_cache_key("gpt-5", messages, None, params={"temperature": 0.9, "max_tokens": 10})
    thinking = llm_cache_key(
        "gpt-5",
        messages,
        None,
        params={"temperature": 0.2, "max_tokens": 10, "reasoning": {"effort": "high"}},
    )
    assert base != hotter
    assert base != thinking


def test_assistant_message_from_cache_content_parses_tool_calls():
    raw = '{"tool_calls": [{"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{}"}}]}'
    msg = assistant_message_from_cache_content(raw)
    assert msg is not None
    assert msg["content"] is None
    assert msg["tool_calls"][0]["function"]["name"] == "t"


class _StubProvider:
    kind = "openai"

    def __init__(self, message: dict) -> None:
        self.message = message
        self.calls = 0

    def chat_completion(self, **kwargs):
        self.calls += 1
        return dict(self.message)

    async def achat_completion(self, **kwargs):
        return self.chat_completion(**kwargs)


def test_cache_provider_misses_when_reasoning_changes(tmp_path) -> None:
    inner = _StubProvider({"role": "assistant", "content": "a"})
    provider = CacheLLMProvider(inner, cache_path=tmp_path / "c.json", allow_read=True, allow_write=True)
    messages = [{"role": "user", "content": "hi"}]
    provider.chat_completion(model="gpt-5", messages=messages, api_key="k", reasoning={"effort": "low"})
    inner.message = {"role": "assistant", "content": "b"}
    out = provider.chat_completion(model="gpt-5", messages=messages, api_key="k", reasoning={"effort": "high"})
    assert out["content"] == "b"
    assert inner.calls == 2
    replay = provider.chat_completion(model="gpt-5", messages=messages, api_key="k", reasoning={"effort": "low"})
    assert replay["content"] == "a"
    assert replay["_mas_cache_status"] == "hit"
    assert inner.calls == 2


def test_live_llm_wraps_cache_provider_when_cache_path_set(tmp_path) -> None:
    cache_path = tmp_path / "llm-cache.json"
    inner = _StubProvider({"role": "assistant", "content": "fresh-response"})
    engine = LiveLlmEngine(
        llm_provider=inner,
        cache_path=cache_path,
        use_cache=True,
        cache_read=False,
        cache_write=True,
    )
    assert isinstance(engine.llm_provider, CacheLLMProvider)
    out = engine._chat_completion([{"role": "user", "content": "hi"}], api_key="", tools=None)
    assert out["content"] == "fresh-response"
    assert cache_path.is_file()
    assert inner.calls == 1

    inner2 = _StubProvider({"role": "assistant", "content": "should-not-hit"})
    second = LiveLlmEngine(
        llm_provider=inner2,
        cache_path=cache_path,
        use_cache=True,
        cache_read=False,
        cache_write=True,
    )
    second._chat_completion([{"role": "user", "content": "hi"}], api_key="", tools=None)
    assert inner2.calls == 1


def test_live_llm_cache_read_hit_skips_inner(tmp_path) -> None:
    cache_path = tmp_path / "llm-cache.json"
    writer_inner = _StubProvider({"role": "assistant", "content": "cached-response"})
    writer = LiveLlmEngine(
        llm_provider=writer_inner,
        cache_path=cache_path,
        use_cache=True,
        cache_read=False,
        cache_write=True,
    )
    writer._chat_completion([{"role": "user", "content": "hello"}], api_key="", tools=None)

    reader_inner = _StubProvider({"role": "assistant", "content": "live"})
    reader = LiveLlmEngine(
        llm_provider=reader_inner,
        cache_path=cache_path,
        use_cache=True,
        cache_read=True,
        cache_write=False,
    )
    out = reader._chat_completion([{"role": "user", "content": "hello"}], api_key="", tools=None)
    assert out["content"] == "cached-response"
    assert reader_inner.calls == 0


def test_live_engine_reports_provider_cache_hit(tmp_path) -> None:
    cache_path = tmp_path / "llm-cache.json"
    writer = LiveLlmEngine(
        llm_provider=_StubProvider({"role": "assistant", "content": "cached-response"}),
        cache_path=cache_path,
        use_cache=True,
        cache_read=False,
        cache_write=True,
    )
    writer.invoke(InvokeEngineIo(correlation_id=1, op="LLM_CALL"))

    reader_inner = _StubProvider({"role": "assistant", "content": "live"})
    reader = LiveLlmEngine(
        llm_provider=reader_inner,
        cache_path=cache_path,
        use_cache=True,
        cache_read=True,
        cache_write=False,
    )
    ret = reader.invoke(InvokeEngineIo(correlation_id=1, op="LLM_CALL"))
    assert ret.text == "cached-response"
    assert ret.cache_status == "hit"
    assert ret.cache_layer == "provider_cache"
    assert reader_inner.calls == 0


def test_live_llm_cache_persists_tool_calls(tmp_path) -> None:
    cache_path = tmp_path / "llm-cache.json"
    tool_calls = [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "demo_tool", "arguments": "{}"},
        }
    ]
    inner = _StubProvider(
        {"role": "assistant", "content": None, "tool_calls": tool_calls, "usage": {"total_tokens": 12}}
    )
    engine = LiveLlmEngine(
        llm_provider=inner,
        cache_path=cache_path,
        use_cache=True,
        cache_read=False,
        cache_write=True,
        use_tool_loop=True,
    )
    engine._chat_completion([{"role": "user", "content": "hi"}], api_key="", tools=[{"type": "function"}])

    reader_inner = _StubProvider({"role": "assistant", "content": "miss"})
    reader = LiveLlmEngine(
        llm_provider=reader_inner,
        cache_path=cache_path,
        use_cache=True,
        cache_read=True,
        cache_write=False,
        use_tool_loop=True,
    )
    out = reader._chat_completion(
        [{"role": "user", "content": "hi"}], api_key="", tools=[{"type": "function"}]
    )
    assert out["tool_calls"][0]["function"]["name"] == "demo_tool"
    assert reader_inner.calls == 0


def test_lookup_preview_falls_back_to_legacy_key_with_one_leaf() -> None:
    preview = "assembled prompt"
    old_key = legacy_preview_cache_key(preview)
    cache = {old_key: "recorded"}
    write_key, entry = lookup_preview_cache(cache, preview, leaf="arm", model="m", api_base="https://a", leaf_count=1)
    assert entry == "recorded"
    assert write_key == bind_route_cache_key(preview, leaf="arm", model="m", api_base="https://a")
    assert write_key != old_key


def test_lookup_preview_ignores_legacy_key_with_several_leaves() -> None:
    preview = "assembled prompt"
    old_key = legacy_preview_cache_key(preview)
    cache = {old_key: "recorded-other-host"}
    write_key, entry = lookup_preview_cache(
        cache, preview, leaf="arm", model="m", api_base="https://a", leaf_count=2
    )
    assert entry is None
    assert write_key == bind_route_cache_key(preview, leaf="arm", model="m", api_base="https://a")
    assert old_key in cache


def test_lookup_preview_prefers_routed_key() -> None:
    preview = "assembled prompt"
    write_key = bind_route_cache_key(preview, leaf="arm", model="m", api_base="https://a")
    old_key = legacy_preview_cache_key(preview)
    cache = {old_key: "legacy", write_key: "routed"}
    found_key, entry = lookup_preview_cache(
        cache, preview, leaf="arm", model="m", api_base="https://a", leaf_count=1
    )
    assert found_key == write_key
    assert entry == "routed"
