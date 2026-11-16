#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""MCP as a protocol layer over the http transport: sugar, headers, claims, close."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from library_ioa.plugins.http_layer import HttpCallTransport, HttpLayer
from library_ioa.plugins.mcp.client import _current_task
from library_ioa.plugins.mcp.layer import McpLayer
from library_ioa.plugins.mcp.provider import MCPToolProvider, _http_inner
from library_ioa.plugins.mcp.spec import MCPProviderSpec
from mas.runtime.layers.compose import declared_protocol_layers, layer_id


class _Dump:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def model_dump(self, mode: str = "json", by_alias: bool = True) -> dict:
        del mode, by_alias
        return dict(self._payload)


class _Session:
    def __init__(self) -> None:
        self.initialize = AsyncMock()
        self.list_tools = AsyncMock(return_value=_Dump({"tools": []}))
        self.call_tool = AsyncMock(
            return_value=_Dump(
                {"content": [{"type": "text", "text": "pong"}], "isError": False, "resultType": "complete"}
            )
        )

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _Transport:
    async def __aenter__(self) -> object:
        return (object(), object())

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _provider(spec: dict, session: _Session | None = None, seen: list | None = None):
    def factory(_url: str, **kw) -> _Transport:
        if seen is not None:
            seen.append(kw.get("http_client"))
        return _Transport()

    return (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", side_effect=factory),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session or _Session()),
    )


def test_protocol_mcp_is_http_then_mcp() -> None:
    spec = {"name": "demo", "protocol": "mcp", "url": "https://mcp.example/mcp", "headers": {"A": "b"}, "timeout": 9}
    steps = McpLayer.protocol_layers(spec)
    assert [layer_id(step) for step in steps] == ["http", "mcp"]
    assert steps[0]["params"] == {
        "follow_redirects": True,
        "url": "https://mcp.example/mcp",
        "headers": {"A": "b"},
        "timeout": 9.0,
    }
    assert [layer_id(step) for step in MCPToolProvider.protocol_layers(spec)] == ["http", "mcp"]
    assert [layer_id(s) for s in declared_protocol_layers("tool_provider", "mcp", spec)] == ["http", "mcp"]


def test_sse_and_stdio_rows_get_no_http_layer() -> None:
    assert [layer_id(s) for s in McpLayer.protocol_layers({"url": "https://x/sse", "transport": "sse"})] == ["mcp"]
    assert [layer_id(s) for s in McpLayer.protocol_layers({"command": "srv"})] == ["mcp"]
    assert _http_inner(MCPProviderSpec.from_dict({"name": "s", "url": "https://x/sse", "transport": "sse"})) is None
    assert _http_inner(MCPProviderSpec.from_dict({"name": "s", "command": "srv"})) is None


def test_http_inner_is_none_without_the_runtime_or_when_the_layer_will_not_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = MCPProviderSpec.from_dict({"name": "s", "url": "https://x/mcp", "headers": {"A": "b"}, "timeout": 3})
    assert isinstance(_http_inner(binding), HttpCallTransport)
    compose = sys.modules["mas.runtime.layers.compose"]

    monkeypatch.setattr(compose, "apply_layer", lambda *a, **k: (_ for _ in ()).throw(compose.LayerLoadError("x")))
    assert _http_inner(binding) is None
    monkeypatch.setitem(sys.modules, "mas.runtime.layers.compose", None)
    assert _http_inner(binding) is None


async def test_mcp_session_uses_the_row_headers_and_the_http_transport() -> None:
    spec = {
        "name": "demo",
        "protocol": "mcp",
        "url": "https://mcp.example/mcp",
        "headers": {"Authorization": "Bearer x"},
        "tools": ["echo"],
    }
    seen: list = []
    session = _Session()
    first, second = _provider(spec, session, seen)
    with first, second:
        provider = MCPToolProvider.from_provider_spec(spec)
        try:
            assert await provider.acall_tool("echo", {"q": 1})
        finally:
            provider.close()
    assert isinstance(seen[0], httpx.AsyncClient) and seen[0].headers["authorization"] == "Bearer x"
    assert session.call_tool.await_count == 1


async def test_call_tool_refuses_a_name_not_advertised_and_an_empty_list_is_nobody() -> None:
    for tools in (["echo"], []):
        spec = {"name": "demo", "protocol": "mcp", "url": "https://mcp.example/mcp", "tools": tools}
        session = _Session()
        first, second = _provider(spec, session)
        with first, second:
            provider = MCPToolProvider.from_provider_spec(spec)
            result = await provider.acall_tool("other", {"q": 1})
            provider.close()
        assert session.call_tool.await_count == 0
        assert result["is_error"] and "other" in result["content"]


def test_wrap_opens_no_client_until_the_session_runs_and_close_stops_the_transport() -> None:
    http = HttpLayer.wrap(None, {"url": "https://mcp.example/mcp"}, call_kinds=("tool",))
    layer = McpLayer.wrap(http, {"url": "https://mcp.example/mcp", "protocol": "mcp", "transport": "http"})
    assert len(http._aclients) == 0 and layer.inner is http
    provider = MCPToolProvider(layer.wrapper, provider_name="demo", tools_claim=["echo"], inner=layer)
    provider.close()
    with pytest.raises(Exception, match="stopped"):
        _ = http.client


def test_an_mcp_layer_without_a_transport_has_none_to_hand_the_client() -> None:
    layer = McpLayer.wrap(None, {"name": "s", "command": "srv"})
    assert layer.inner is None


def test_current_task_is_none_once_the_loop_is_gone() -> None:
    assert _current_task() is None
