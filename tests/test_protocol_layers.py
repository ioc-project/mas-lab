#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""MCP and OpenAI as protocol layers over http. protocol: plus a url is sugar for that stack."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from library_ioa.plugins.http_layer import HttpCallTransport, HttpLayer
from library_ioa.plugins.mcp.layer import McpLayer
from library_ioa.plugins.mcp.provider import MCPToolProvider
from mas.library.standard.lib.stack import Envelope, Layer, Stack
from mas.library.standard.plugins.llm.openai import OpenAILLMProvider
from mas.runtime.layers.compose import declared_protocol_layers, layer_id, merge_protocol_layers


def _chat_handler(recorded: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(200, json=recorded)

    return handler


def test_openai_over_http_matches_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = {"choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}]}
    monkeypatch.setenv("K", "secret")
    transport = httpx.MockTransport(_chat_handler(recorded))
    direct = OpenAILLMProvider(api_base="http://127.0.0.1:9/v1", api_key_env="K")
    direct._client = httpx.Client(transport=transport)
    http = HttpLayer.wrap(None, {"url": "http://127.0.0.1:9/v1"}, call_kinds=("llm",))
    assert isinstance(http, HttpCallTransport)
    http._client = httpx.Client(transport=transport)
    layered = OpenAILLMProvider.wrap(http, {"api_base": "http://127.0.0.1:9/v1", "api_key_env": "K"})
    kwargs = {"model": "m", "messages": [{"role": "user", "content": "q"}]}
    assert layered.chat_completion(**kwargs) == direct.chat_completion(**kwargs)


def test_openai_http_cache_fixture_matches_direct(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = {"choices": [{"message": {"role": "assistant", "content": "cached"}, "finish_reason": "stop"}]}
    monkeypatch.setenv("K", "secret")
    transport = httpx.MockTransport(_chat_handler(recorded))
    http = HttpLayer.wrap(None, {"url": "http://127.0.0.1:9/v1"}, call_kinds=("llm",))
    http._client = httpx.Client(transport=transport)
    inner = OpenAILLMProvider.wrap(http, {"api_base": "http://127.0.0.1:9/v1", "api_key_env": "K"})
    cache_path = tmp_path / "c.json"
    from mas.runtime.registry.llm_provider_registry import wrap_llm_provider_cache

    writer = wrap_llm_provider_cache(inner, cache_path=cache_path, cache_read=False, cache_write=True)
    written = writer.chat_completion(model="m", messages=[{"role": "user", "content": "q"}])
    reader_http = HttpLayer.wrap(None, {"url": "http://127.0.0.1:9/v1"}, call_kinds=("llm",))

    def boom(request: httpx.Request) -> httpx.Response:
        raise AssertionError("cache hit must not POST")

    reader_http._client = httpx.Client(transport=httpx.MockTransport(boom))
    reader_inner = OpenAILLMProvider.wrap(reader_http, {"api_base": "http://127.0.0.1:9/v1", "api_key_env": "K"})
    reader = wrap_llm_provider_cache(reader_inner, cache_path=cache_path, cache_read=True, cache_write=False)
    cached = reader.chat_completion(model="m", messages=[{"role": "user", "content": "q"}])
    assert cached["content"] == written["content"]
    assert cached.get("_mas_cache_status") == "hit"


def test_protocol_openai_is_sugar_for_openai_over_http() -> None:
    steps = OpenAILLMProvider.protocol_layers({"protocol": "openai", "api_base": "http://127.0.0.1:9/v1", "timeout": 7})
    assert [layer_id(step) for step in steps] == ["http", "openai"]
    assert steps[0]["params"]["url"] == "http://127.0.0.1:9/v1"
    merged = merge_protocol_layers(
        [{"layer": "retry", "for": ["llm"]}],
        plugin_type="llm_provider",
        kind="openai",
        spec={"api_base": "http://127.0.0.1:9/v1"},
    )
    assert [layer_id(step) for step in merged] == ["http", "openai", "retry"]


class _Dump:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def model_dump(self, mode: str = "json", by_alias: bool = True) -> dict:
        del mode
        assert by_alias is True
        return dict(self._payload)


class _Session:
    def __init__(self, call_result: _Dump | None = None) -> None:
        self.initialize = AsyncMock()
        self.list_tools = AsyncMock(return_value=_Dump({"tools": []}))
        self.call_tool = AsyncMock(
            return_value=call_result
            or _Dump({"content": [{"type": "text", "text": "pong"}], "isError": False, "resultType": "complete"})
        )

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        del exc
        return False


class _Transport:
    async def __aenter__(self) -> object:
        return (object(), object())

    async def __aexit__(self, *exc: object) -> bool:
        del exc
        return False


def test_mcp_over_http_matches_old_provider() -> None:
    spec = {
        "name": "demo",
        "protocol": "mcp",
        "url": "http://127.0.0.1:9/mcp",
        "tools": ["echo"],
    }
    clients: list[object] = []

    def factory(_url: str, **kw) -> _Transport:
        clients.append(kw.get("http_client"))
        return _Transport()

    session = _Session()
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", side_effect=factory),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        old = MCPToolProvider.from_provider_spec(spec)
        http = HttpLayer.wrap(None, {"url": spec["url"]}, call_kinds=("tool",))
        layer = McpLayer.wrap(http, spec)
        layered = MCPToolProvider(layer.wrapper, provider_name="demo", tools_claim=["echo"], inner=layer)
        try:
            assert layered.list_tools() == old.list_tools()
            assert old.call_tool("echo", {"q": 1}) == layered.call_tool("echo", {"q": 1})
        finally:
            old.close()
            layered.close()
    assert [layer_id(step) for step in MCPToolProvider.protocol_layers(spec)] == ["http", "mcp"]
    assert any(client is not None for client in clients)
    assert old.inner is not None


def test_send_stamps_traceparent() -> None:
    seen: list[Envelope] = []

    class Wire(Layer):
        needs_lower = False

        def send(self, envelope: Envelope) -> str:
            seen.append(envelope)
            return "ok"

    Stack([Wire()]).send(Envelope(payload="hi"))
    assert seen and "traceparent" in dict(seen[0].headers)


def test_declared_mcp_layers_come_from_the_plugin() -> None:
    steps = declared_protocol_layers("tool_provider", "mcp", {"url": "http://127.0.0.1:9/mcp", "protocol": "mcp"})
    assert [layer_id(step) for step in steps] == ["http", "mcp"]


def test_apply_layer_http_for_llm_is_layerbase(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    from mas.runtime.deprecation import reset_deprecation_warnings
    from mas.runtime.layers.base import LayerBase
    from mas.runtime.layers.compose import apply_layer

    reset_deprecation_warnings()
    with caplog.at_level(logging.WARNING, logger="mas.runtime"):
        wrapped = apply_layer(None, {"layer": "http", "for": ["llm"], "params": {"url": "http://127.0.0.1:9/v1"}})
    assert isinstance(wrapped, HttpCallTransport)
    assert isinstance(wrapped, LayerBase)
    assert "LayerBase" not in caplog.text


def test_wrap_llm_provider_cache_is_the_cache_layer_and_keys_on_the_chat(tmp_path) -> None:
    from mas.library.standard.plugins.infra.llm_cache import LlmCacheMiddleware
    from mas.library.standard.plugins.llm.cache import CacheLLMProvider
    from mas.runtime.registry.llm_provider_registry import wrap_llm_provider_cache

    class _Stub:
        kind = "openai"
        calls: list[str]

        def __init__(self) -> None:
            self.calls = []

        def chat_completion(self, **kwargs):
            text = str((kwargs.get("messages") or [{}])[-1].get("content") or "")
            self.calls.append(text)
            return {"role": "assistant", "content": text}

        async def achat_completion(self, **kwargs):
            return self.chat_completion(**kwargs)

    inner = _Stub()
    wrapped = wrap_llm_provider_cache(inner, cache_path=tmp_path / "c.json", cache_read=True, cache_write=True)
    assert not isinstance(wrapped, CacheLLMProvider)
    cur = getattr(wrapped, "_stack", wrapped)
    cache = None
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, LlmCacheMiddleware):
            cache = cur
            break
        cur = getattr(cur, "inner", None)
    assert cache is not None
    first = wrapped.chat_completion(model="m", messages=[{"role": "user", "content": "a"}])
    second = wrapped.chat_completion(model="m", messages=[{"role": "user", "content": "b"}])
    assert inner.calls == ["a", "b"]
    replay = wrapped.chat_completion(model="m", messages=[{"role": "user", "content": "a"}])
    assert inner.calls == ["a", "b"]
    assert first["content"] == "a"
    assert second["content"] == "b"
    assert replay["content"] == "a"
    assert replay.get("_mas_cache_status") == "hit"


def test_openai_from_infra_does_not_leave_a_second_live_http_inner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("K", "secret")
    spec = {"api_base": "http://127.0.0.1:9/v1", "api_key_env": "K", "kind": "openai"}
    created: list[HttpCallTransport] = []
    original = HttpCallTransport.from_params

    def tracking(params: dict) -> HttpCallTransport:
        transport = original(params)
        created.append(transport)
        return transport

    monkeypatch.setattr(HttpCallTransport, "from_params", classmethod(lambda cls, params: tracking(params)))
    provider = OpenAILLMProvider.from_infra_spec(spec)
    from mas.runtime.engine.infra_pipeline import wrap_llm_provider_pipeline
    from mas.runtime.layers.compose import merge_protocol_layers

    pipeline = merge_protocol_layers([], plugin_type="llm_provider", kind="openai", spec=spec)
    wrap_llm_provider_pipeline(provider, pipeline)
    assert len(created) == 1
    assert provider.inner is created[0]


def test_mcp_call_tool_refuses_a_name_not_in_tools() -> None:
    spec = {"name": "demo", "protocol": "mcp", "url": "http://127.0.0.1:9/mcp", "tools": ["echo"]}
    session = _Session()
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        provider = MCPToolProvider.from_provider_spec(spec)
        names = [row["name"] for row in provider.list_tools()]
        assert names == ["echo"]
        result = provider.call_tool("other", {"q": 1})
    assert session.call_tool.await_count == 0
    text = result if isinstance(result, str) else str(result)
    assert "echo" in names
    assert "other" in text.lower() or "not" in text.lower() or "unclaim" in text.lower()


def test_mcp_empty_tools_list_is_nobody() -> None:
    spec = {"name": "demo", "protocol": "mcp", "url": "http://127.0.0.1:9/mcp", "tools": []}
    session = _Session()
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        provider = MCPToolProvider.from_provider_spec(spec)
        assert provider.list_tools() == []
        result = provider.call_tool("echo", {"q": 1})
    assert session.call_tool.await_count == 0
    text = result if isinstance(result, str) else str(result)
    assert text


def test_http_layer_wrap_keeps_an_existing_call_transport() -> None:
    http = HttpCallTransport.from_params({"url": "http://127.0.0.1:9/v1"})
    assert HttpLayer.wrap(http, {"url": "http://127.0.0.1:8/v1"}, call_kinds=("llm",)) is http


def test_openai_wrap_http_is_kept_when_pipeline_binds_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("K", "secret")
    http = HttpCallTransport.from_params({"url": "http://127.0.0.1:9/v1"})
    provider = OpenAILLMProvider.wrap(http, {"api_base": "http://127.0.0.1:9/v1", "api_key_env": "K"})
    from mas.runtime.engine.infra_pipeline import wrap_llm_provider_pipeline
    from mas.runtime.layers.compose import merge_protocol_layers

    spec = {"api_base": "http://127.0.0.1:9/v1", "api_key_env": "K", "kind": "openai"}
    pipeline = merge_protocol_layers([], plugin_type="llm_provider", kind="openai", spec=spec)
    wrap_llm_provider_pipeline(provider, pipeline)
    assert provider.inner is http


def test_mcp_wrap_does_not_open_http_aclient_before_the_session() -> None:
    http = HttpCallTransport.from_params({"url": "http://127.0.0.1:9/mcp"})
    layer = McpLayer.wrap(http, {"url": "http://127.0.0.1:9/mcp", "protocol": "mcp"})
    assert len(http._aclients) == 0
    assert layer.inner is http


def test_mcp_close_stops_the_http_inner() -> None:
    spec = {"name": "demo", "protocol": "mcp", "url": "http://127.0.0.1:9/mcp", "tools": ["echo"]}
    session = _Session()
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        provider = MCPToolProvider.from_provider_spec(spec)
        http = getattr(provider.inner, "inner", None)
        assert isinstance(http, HttpCallTransport)
        provider.close()
    with pytest.raises(Exception, match="stopped"):
        _ = http.aclient


def test_wrap_tool_pipeline_refuses_http_and_mcp_steps() -> None:
    from mas.runtime.engine.infra_pipeline import wrap_tool_pipeline

    with pytest.raises(ValueError, match="http"):
        wrap_tool_pipeline(
            object(),
            [{"layer": "http", "for": ["tool"]}, {"layer": "mcp", "for": ["tool"]}],
        )


def test_engine_stop_closes_the_tool_provider() -> None:
    from mas.runtime.driver.mocks import AutoCtxAssembler
    from mas.runtime.engine.llm_live import LiveLlmEngine

    closed: list[bool] = []

    class _Provider:
        def close(self) -> None:
            closed.append(True)

    engine = LiveLlmEngine(
        ctx=AutoCtxAssembler(),
        api_base="http://127.0.0.1:9/v1",
        api_key_env="K",
        model="m",
        tool_provider=_Provider(),
    )
    engine.stop()
    assert closed


def _inner_for(api_base: str, **extra: object):
    from mas.runtime.engine.infra_pipeline import wrap_llm_provider_pipeline

    spec = {"api_base": api_base, "api_key_env": "K", "kind": "openai", **extra}
    provider = OpenAILLMProvider.from_infra_spec(spec)
    wrap_llm_provider_pipeline(
        provider, merge_protocol_layers([], plugin_type="llm_provider", kind="openai", spec=spec)
    )
    return provider


def test_protocol_openai_accepts_plain_http_on_an_internal_host() -> None:
    provider = _inner_for("http://llm.internal:8000/v1")
    assert isinstance(provider.inner, HttpCallTransport)


def test_protocol_openai_keeps_the_provider_timeout_default() -> None:
    provider = _inner_for("http://127.0.0.1:9/v1")
    assert provider.inner.timeout == provider._timeout() == 120.0
    assert _inner_for("http://127.0.0.1:9/v1", timeout=7).inner.timeout == 7.0


def test_protocol_openai_keeps_tls_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _inner_for("https://llm.example/v1", verify_ssl=False)
    assert provider.inner._verify is False
    assert provider.inner.client.timeout.read == 120.0


def test_bind_twice_replaces_the_stack_it_put_on() -> None:
    from mas.ctl.compose.models import ResolvedInfra
    from mas.ctl.infra.bind import bind_flavour
    from mas.runtime.layers.compose import stamp_layer_step

    retry = stamp_layer_step({"layer": "retry", "for": ["llm"], "params": {"max_attempts": 3}})
    resolved = ResolvedInfra(llm_proxy={"api_base": "http://127.0.0.1:1/v1", "pipeline": []}, stacks={"s": [retry]})
    flavour = {"bind": {"llm": {"stack": "s"}}}
    once = bind_flavour(resolved, flavour, agent_ids=["a"])
    twice = bind_flavour(once, flavour, agent_ids=["a"])
    assert len(once.llm_proxy["pipeline"]) == len(twice.llm_proxy["pipeline"]) == 1
    none = bind_flavour(twice, {"bind": {}}, agent_ids=["a"])
    assert none.llm_proxy["pipeline"] == []


def test_bind_tools_stack_refuses_http_and_mcp() -> None:
    from mas.ctl.compose.models import ResolvedInfra
    from mas.ctl.infra.bind import BindError, bind_flavour
    from mas.runtime.layers.compose import stamp_layer_step

    steps = [stamp_layer_step({"layer": "http", "for": ["tool"]})]
    resolved = ResolvedInfra(llm_proxy={"api_base": "http://127.0.0.1:1/v1"}, stacks={"t": steps})
    with pytest.raises(BindError, match="tool-server row"):
        bind_flavour(resolved, {"bind": {"tools": {"stack": "t"}}}, agent_ids=["a"])


def test_tool_server_row_may_list_http_and_mcp_the_provider_already_built() -> None:
    from mas.runtime.engine.infra_pipeline import filter_pipeline_for_kind, split_pipeline_layers
    from mas.runtime.layers.compose import layer_role, stamp_layer_step

    steps = [stamp_layer_step({"layer": name, "for": ["tool"]}) for name in ("http", "mcp")]
    _, per_dest = split_pipeline_layers(filter_pipeline_for_kind(steps, "tool"))
    assert [layer_role(step) for step in per_dest] == ["transport", "protocol"] or per_dest == []


def test_a_replayed_json_answer_is_not_taken_for_a_completion() -> None:
    from mas.runtime.engine.infra_pipeline import _completion_from_cached_text

    assert _completion_from_cached_text('{"role": "assistant", "content": "x"}')["content"] == "x"
    with pytest.raises(RuntimeError, match="no completion"):
        _completion_from_cached_text('{"answer": 42}')
    with pytest.raises(RuntimeError, match="no completion"):
        _completion_from_cached_text("plain text")


def test_provider_exchange_preview_is_the_chat_cache_key() -> None:
    from mas.runtime.engine.infra_pipeline import _PROVIDER_CALL, _ProviderCallEngine
    from mas.runtime.engine.llm_cache import completion_cache_params, llm_cache_key

    payload = {"model": "m", "messages": [{"role": "user", "content": "q"}], "temperature": 0.2, "max_tokens": 5}
    token = _PROVIDER_CALL.set(payload)
    try:
        key = _ProviderCallEngine(inner=object()).exchange_preview("LLM_CALL")
    finally:
        _PROVIDER_CALL.reset(token)
    expected = llm_cache_key(
        "m", payload["messages"], None, params=completion_cache_params(temperature=0.2, max_tokens=5)
    )
    assert key == expected


def test_cache_llm_provider_kind_is_deprecated(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    from mas.library.standard.plugins.llm.cache import CacheLLMProvider
    from mas.runtime.deprecation import reset_deprecation_warnings

    reset_deprecation_warnings()
    with caplog.at_level(logging.WARNING):
        CacheLLMProvider(object(), cache_path=None, allow_read=False, allow_write=False)
    assert "llm_cache layer" in caplog.text
