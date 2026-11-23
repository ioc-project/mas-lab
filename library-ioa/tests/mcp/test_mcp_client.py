#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from library_ioa.plugins.mcp.client import MCPClient, _streams_from_transport
from library_ioa.plugins.mcp.client.wrapper import MCPClientWrapper


class _Dump:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def model_dump(self, mode: str = "json", by_alias: bool = True) -> dict:
        assert by_alias is True
        return dict(self._payload)


class _ListResult:
    def __init__(self, tools: list[_Dump], next_cursor: str | None = None, ttl_ms: int | None = None) -> None:
        self.tools = tools
        self.next_cursor = next_cursor
        self.ttl_ms = ttl_ms


class _Session:
    def __init__(self, list_pages: list[_ListResult] | None = None, call_result: _Dump | None = None) -> None:
        self.initialize = AsyncMock()
        self._list_pages = list(list_pages or [])
        self.list_tools = AsyncMock(side_effect=self._next_page)
        self.call_tool = AsyncMock(
            return_value=call_result
            or _Dump({"content": [{"type": "text", "text": "ok"}], "isError": False, "resultType": "complete"})
        )

    def _next_page(self, params=None):
        if not self._list_pages:
            return _ListResult([])
        return self._list_pages.pop(0)

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _Transport:
    def __init__(self, streams: object | None = None) -> None:
        self.exits = 0
        self._streams = streams if streams is not None else (object(), object())

    async def __aenter__(self) -> object:
        return self._streams

    async def __aexit__(self, *exc: object) -> bool:
        self.exits += 1
        return False


def test_streams_from_transport_requires_read_write() -> None:
    assert _streams_from_transport((1, 2, 3)) == (1, 2)
    with pytest.raises(TypeError, match="unexpected MCP transport"):
        _streams_from_transport(object())


def test_list_and_call_each_close_http_transport() -> None:
    client = MCPClient(url="http://127.0.0.1:9001/mcp")
    transports: list[_Transport] = []
    session = _Session(list_pages=[_ListResult([_Dump({"name": "web-search", "inputSchema": {"type": "object"}})])])

    def _factory(_url: str, **_kw) -> _Transport:
        cm = _Transport()
        transports.append(cm)
        return cm

    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", side_effect=_factory),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        tools = asyncio.run(client.list_tools())
        payload = asyncio.run(client.call_tool("web-search", {"query": "Apple"}))

    assert tools == [{"name": "web-search", "inputSchema": {"type": "object"}}]
    assert payload["isError"] is False
    assert payload["content"][0]["text"] == "ok"
    assert len(transports) == 2
    assert all(t.exits == 1 for t in transports)
    session.call_tool.assert_awaited()
    assert "read_timeout_seconds" not in session.call_tool.call_args.kwargs


def test_list_and_call_emit_when_hooks_are_on() -> None:
    from mas.runtime.schema.transport import TransportEvent
    from mas.runtime.transport import emit
    from mas.runtime.transport.config import ProtocolTransportConfig, TransportObservabilityConfig

    captured: list[TransportEvent] = []

    class Sink:
        def emit(self, event: TransportEvent) -> None:
            captured.append(event)

    emit.reset()
    emit.bind_config(TransportObservabilityConfig(present=True, defaults=ProtocolTransportConfig()))
    emit.bind_sink(Sink())
    client = MCPClient(url="http://127.0.0.1:9001/mcp")
    session = _Session(list_pages=[_ListResult([_Dump({"name": "web-search", "inputSchema": {"type": "object"}})])])
    try:
        with (
            patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
            patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
        ):
            asyncio.run(client.list_tools(agent_id="worker"))
            asyncio.run(client.list_tools(agent_id="worker"))  # cached: discovery already announced
            asyncio.run(client.call_tool("web-search", {"query": "Apple"}))
            session.call_tool = AsyncMock(side_effect=RuntimeError("down"))
            with pytest.raises(RuntimeError):
                asyncio.run(client.call_tool("web-search", {}))
    finally:
        emit.reset()
    kinds = [e.kind for e in captured]
    assert "transport.discovery" in kinds
    assert "transport.message.sent" in kinds
    assert "transport.message.completed" in kinds
    assert "transport.error" in kinds
    assert all(e.agent_id != "ctx.user" for e in captured if e.kind == "transport.discovery")


def test_call_tool_forwards_timeout_and_meta() -> None:
    client = MCPClient(url="http://127.0.0.1:9001/mcp", timeout=9)
    session = _Session()
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        asyncio.run(client.call_tool("web-search", {}, timeout_seconds=3, meta={"trace": "1"}))
    assert session.call_tool.call_args.kwargs["read_timeout_seconds"] == 3
    assert session.call_tool.call_args.kwargs["meta"] == {"trace": "1"}


def test_headers_use_create_mcp_http_client() -> None:
    client = MCPClient(
        url="http://127.0.0.1:9001/mcp",
        headers={"Authorization": "Bearer x"},
        timeout=2.5,
        transport="streamable-http",
    )
    http_clients: list[object] = []
    transports: list[_Transport] = []

    class _Httpx:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            http_clients.append(self)
            self.exits = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc: object) -> bool:
            self.exits += 1
            return False

    def _factory(_url: str, **kw) -> _Transport:
        assert "http_client" in kw
        cm = _Transport()
        transports.append(cm)
        return cm

    session = _Session(list_pages=[_ListResult([_Dump({"name": "t"})])])
    with (
        patch("library_ioa.plugins.mcp.client.create_mcp_http_client", side_effect=_Httpx),
        patch("library_ioa.plugins.mcp.client.streamable_http_client", side_effect=_factory),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        asyncio.run(client.list_tools())

    assert http_clients[0].kwargs["headers"] == {"Authorization": "Bearer x"}
    assert "timeout" not in http_clients[0].kwargs
    assert transports[0].exits == 1
    assert http_clients[0].exits == 1


def test_provided_http_client_is_passed_to_streamable_http() -> None:
    provided = object()
    client = MCPClient(url="http://127.0.0.1:9001/mcp", http_client=provided)
    captured: dict[str, object] = {}

    def _factory(_url: str, **kw) -> _Transport:
        captured["kwargs"] = kw
        return _Transport()

    session = _Session(list_pages=[_ListResult([_Dump({"name": "t"})])])
    with (
        patch("library_ioa.plugins.mcp.client.create_mcp_http_client") as factory,
        patch("library_ioa.plugins.mcp.client.streamable_http_client", side_effect=_factory),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        asyncio.run(client.list_tools())

    factory.assert_not_called()
    assert captured["kwargs"]["http_client"] is provided


def test_timeout_does_not_create_custom_http_client() -> None:
    client = MCPClient(url="http://127.0.0.1:9001/mcp", timeout=30)
    captured: dict[str, object] = {}

    def _factory(_url: str, **kw) -> _Transport:
        captured["kwargs"] = kw
        return _Transport()

    session = _Session(list_pages=[_ListResult([_Dump({"name": "t"})])])
    with (
        patch("library_ioa.plugins.mcp.client.create_mcp_http_client") as factory,
        patch("library_ioa.plugins.mcp.client.streamable_http_client", side_effect=_factory),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        asyncio.run(client.list_tools())

    factory.assert_not_called()
    assert "http_client" not in captured["kwargs"]


def test_follow_pagination_false_stops_after_first_page() -> None:
    client = MCPClient(url="http://127.0.0.1:9001/mcp", follow_pagination=False)
    session = _Session(
        list_pages=[
            _ListResult([_Dump({"name": "a"})], next_cursor="page-2"),
            _ListResult([_Dump({"name": "b"})], next_cursor=None),
        ]
    )
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        tools = asyncio.run(client.list_tools())
    assert [t["name"] for t in tools] == ["a"]
    assert session.list_tools.await_count == 1
    client = MCPClient(url="http://127.0.0.1:9001/mcp")
    session = _Session(
        list_pages=[
            _ListResult([_Dump({"name": "a"})], next_cursor="page-2"),
            _ListResult([_Dump({"name": "b"})], next_cursor=None),
        ]
    )
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        tools = asyncio.run(client.list_tools())
    assert [t["name"] for t in tools] == ["a", "b"]
    assert session.list_tools.await_count == 2


def test_list_tools_stops_on_repeated_cursor() -> None:
    client = MCPClient(url="http://127.0.0.1:9001/mcp")
    session = _Session(
        list_pages=[
            _ListResult([_Dump({"name": "a"})], next_cursor="loop"),
            _ListResult([_Dump({"name": "b"})], next_cursor="loop"),
        ]
    )
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        tools = asyncio.run(client.list_tools())
    assert [t["name"] for t in tools] == ["a", "b"]
    assert session.list_tools.await_count == 2


def test_sse_transport_uses_sse_client() -> None:
    client = MCPClient(url="http://127.0.0.1:9001/sse", transport="sse", headers={"X": "1"}, timeout=4)
    session = _Session(list_pages=[_ListResult([_Dump({"name": "sse-tool"})])])
    with (
        patch("library_ioa.plugins.mcp.client.sse_client", return_value=_Transport()) as sse,
        patch("library_ioa.plugins.mcp.client.streamable_http_client") as http,
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        tools = asyncio.run(client.list_tools())
    assert tools[0]["name"] == "sse-tool"
    sse.assert_called_once()
    assert sse.call_args.kwargs["headers"] == {"X": "1"}
    assert "timeout" not in sse.call_args.kwargs
    http.assert_not_called()


def test_stdio_transport_uses_command() -> None:
    client = MCPClient(command="python", args=["-m", "demo"], env={"A": "1"}, cwd="/tmp", transport="stdio")
    session = _Session(list_pages=[_ListResult([_Dump({"name": "local"})])])
    captured = {}

    def _stdio(params, **_kw):
        captured["command"] = params.command
        captured["args"] = params.args
        captured["env"] = params.env
        captured["cwd"] = params.cwd
        return _Transport()

    with (
        patch("library_ioa.plugins.mcp.client.stdio_client", side_effect=_stdio),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        tools = asyncio.run(client.list_tools())
    assert tools[0]["name"] == "local"
    assert captured["command"] == "python"
    assert captured["args"] == ["-m", "demo"]
    assert captured["env"] == {"A": "1"}
    assert str(captured["cwd"]) == "/tmp"


def test_stdio_without_command_raises() -> None:
    client = MCPClient(transport="stdio")
    with pytest.raises(ValueError, match="command"):
        asyncio.run(client.list_tools())


def test_sse_without_url_raises() -> None:
    client = MCPClient(transport="sse")
    with pytest.raises(ValueError, match="url"):
        asyncio.run(client.list_tools())


def test_connect_then_disconnect_without_calls_is_harmless() -> None:
    client = MCPClient(url="http://127.0.0.1:9001/mcp")
    assert asyncio.run(client.connect()) is None
    assert asyncio.run(client.disconnect()) is None


def test_hold_closed_off_the_loop_does_not_raise() -> None:
    client = MCPClient(url="http://127.0.0.1:9001/mcp")

    async def make() -> object:
        return client._hold(asyncio.get_running_loop().create_future(), asyncio.Event())

    coro = asyncio.run(make())
    coro.close()


def test_connect_reuses_one_http_transport_on_the_same_loop() -> None:
    client = MCPClient(url="http://127.0.0.1:9001/mcp")
    transports: list[_Transport] = []
    session = _Session(list_pages=[_ListResult([_Dump({"name": "web-search", "inputSchema": {"type": "object"}})])])

    def _factory(_url: str, **_kw) -> _Transport:
        cm = _Transport()
        transports.append(cm)
        return cm

    async def _both() -> tuple[list, dict]:
        await client.connect()
        tools = await client.list_tools()
        payload = await client.call_tool("web-search", {"query": "Apple"})
        await client.disconnect()
        return tools, payload

    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", side_effect=_factory),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        tools, payload = asyncio.run(_both())

    assert tools == [{"name": "web-search", "inputSchema": {"type": "object"}}]
    assert payload["content"][0]["text"] == "ok"
    assert len(transports) == 1
    assert transports[0].exits == 1
    assert session.initialize.await_count == 1


def test_wrapper_run_reuses_one_http_transport() -> None:
    inner = MCPClient(url="http://127.0.0.1:9001/mcp")
    wrapper = MCPClientWrapper(inner)
    transports: list[_Transport] = []
    session = _Session(list_pages=[_ListResult([_Dump({"name": "web-search"})])])

    def _factory(_url: str, **_kw) -> _Transport:
        cm = _Transport()
        transports.append(cm)
        return cm

    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", side_effect=_factory),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        tools = wrapper.run(inner.list_tools())
        payload = wrapper.run(inner.call_tool("web-search", {}))
        wrapper.close()

    assert tools[0]["name"] == "web-search"
    assert payload["isError"] is False
    assert len(transports) == 1
    assert transports[0].exits == 1
    assert session.initialize.await_count == 1


def test_wrapper_delegates_to_client() -> None:
    inner = MCPClient(url="http://127.0.0.1:9001/mcp")
    wrapper = MCPClientWrapper(inner)
    session = _Session(list_pages=[_ListResult([_Dump({"name": "t"})])])

    async def _all() -> tuple[list, dict]:
        await wrapper.connect()
        tools = await wrapper.list_tools()
        payload = await wrapper.call_tool("t", {})
        await wrapper.disconnect()
        return tools, payload

    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        tools, payload = asyncio.run(_all())
    assert tools[0]["name"] == "t"
    assert payload["isError"] is False


def test_list_tools_honors_ttl_and_invalidate() -> None:
    client = MCPClient(url="http://127.0.0.1:9001/mcp", cache_ttl_ms=60_000)
    first = _Session(list_pages=[_ListResult([_Dump({"name": "a"})])])
    second = _Session(list_pages=[_ListResult([_Dump({"name": "b"})])])
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=first),
    ):
        assert [t["name"] for t in asyncio.run(client.list_tools())] == ["a"]
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=second),
    ):
        assert [t["name"] for t in asyncio.run(client.list_tools())] == ["a"]
        client.invalidate_list_cache()
        assert [t["name"] for t in asyncio.run(client.list_tools())] == ["b"]


def _real_result(*, ttl_ms: int | None = None) -> Any:
    from mcp.types import ListToolsResult, Tool

    tool = Tool(name="a", inputSchema={"type": "object"})
    if ttl_ms is None:
        return ListToolsResult.model_validate({"tools": [tool.model_dump(by_alias=True)]})
    return ListToolsResult.model_validate({"tools": [tool.model_dump(by_alias=True)], "ttlMs": ttl_ms})


def test_list_without_ttl_is_listed_once_per_client() -> None:
    """The SDK result fills an absent ttlMs with 0; that must not read as 'do not cache'."""
    client = MCPClient(url="http://127.0.0.1:9001/mcp")
    session = _Session(list_pages=[_real_result(), _real_result()])
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        for _ in range(5):
            assert [t["name"] for t in asyncio.run(client.list_tools())] == ["a"]
    assert session.list_tools.await_count == 1


def test_list_with_ttl_zero_from_the_server_is_not_cached() -> None:
    client = MCPClient(url="http://127.0.0.1:9001/mcp")
    session = _Session(list_pages=[_real_result(ttl_ms=0), _real_result(ttl_ms=0)])
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        asyncio.run(client.list_tools())
        asyncio.run(client.list_tools())
    assert session.list_tools.await_count == 2


def test_tool_list_changed_notification_drops_the_cached_list() -> None:
    from mcp.types import ToolListChangedNotification

    client = MCPClient(url="http://127.0.0.1:9001/mcp")
    session = _Session(list_pages=[_real_result(), _real_result()])
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        asyncio.run(client.list_tools())
        asyncio.run(client.list_tools())
        assert session.list_tools.await_count == 1
        asyncio.run(client._on_session_message(ToolListChangedNotification()))
        asyncio.run(client.list_tools())
    assert session.list_tools.await_count == 2


def test_closing_a_held_session_rediscovers_the_list() -> None:
    client = MCPClient(url="http://127.0.0.1:9001/mcp")
    session = _Session(list_pages=[_real_result(), _real_result()])

    async def scenario() -> None:
        await client.connect()
        await client.list_tools()
        await client.list_tools()
        assert session.list_tools.await_count == 1
        await client.disconnect()
        await client.list_tools()

    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        asyncio.run(scenario())
    assert session.list_tools.await_count == 2


def test_list_tools_ttl_zero_skips_cache() -> None:
    client = MCPClient(url="http://127.0.0.1:9001/mcp", cache_ttl_ms=0)
    first = _Session(list_pages=[_ListResult([_Dump({"name": "a"})])])
    second = _Session(list_pages=[_ListResult([_Dump({"name": "b"})])])
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=first),
    ):
        assert [t["name"] for t in asyncio.run(client.list_tools())] == ["a"]
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=second),
    ):
        assert [t["name"] for t in asyncio.run(client.list_tools())] == ["b"]


def test_list_tools_ttl_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MCPClient(url="http://127.0.0.1:9001/mcp", cache_ttl_ms=10)
    clock = {"t": 1000.0}
    monkeypatch.setattr("library_ioa.plugins.mcp.client.time.monotonic", lambda: clock["t"])
    first = _Session(list_pages=[_ListResult([_Dump({"name": "a"})])])
    second = _Session(list_pages=[_ListResult([_Dump({"name": "b"})])])
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=first),
    ):
        assert [t["name"] for t in asyncio.run(client.list_tools())] == ["a"]
    clock["t"] = 1000.02
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=second),
    ):
        assert [t["name"] for t in asyncio.run(client.list_tools())] == ["b"]


def test_list_tools_honors_server_ttl_ms(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MCPClient(url="http://127.0.0.1:9001/mcp")
    clock = {"t": 1000.0}
    monkeypatch.setattr("library_ioa.plugins.mcp.client.time.monotonic", lambda: clock["t"])
    first = _Session(list_pages=[_ListResult([_Dump({"name": "a"})], ttl_ms=10)])
    second = _Session(list_pages=[_ListResult([_Dump({"name": "b"})])])
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=first),
    ):
        assert [t["name"] for t in asyncio.run(client.list_tools())] == ["a"]
    clock["t"] = 1000.02
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=second),
    ):
        assert [t["name"] for t in asyncio.run(client.list_tools())] == ["b"]


def test_list_tools_private_cache_is_per_user() -> None:
    client = MCPClient(url="http://127.0.0.1:9001/mcp", cache_scope="private")
    alice = _Session(list_pages=[_ListResult([_Dump({"name": "a"})])])
    bob = _Session(list_pages=[_ListResult([_Dump({"name": "b"})])])
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=alice),
    ):
        assert [t["name"] for t in asyncio.run(client.list_tools(user="alice"))] == ["a"]
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=bob),
    ):
        assert [t["name"] for t in asyncio.run(client.list_tools(user="bob"))] == ["b"]
        assert [t["name"] for t in asyncio.run(client.list_tools(user="alice"))] == ["a"]


def test_list_tools_emits_discovery_once_hooks_are_on() -> None:
    """Mutation: tools/list while the bus is off is cached forever, so later capture never emits."""
    from library_ioa.plugins.mcp.hooks import PROTOCOL
    from mas.runtime.schema.transport import DISCOVERY, TransportEvent
    from mas.runtime.transport import emit
    from mas.runtime.transport.config import ProtocolTransportConfig, TransportObservabilityConfig

    client = MCPClient(url="http://127.0.0.1:9001/mcp")
    session = _Session(list_pages=[_ListResult([_Dump({"name": "web-search"})])])
    captured: list[TransportEvent] = []

    class Sink:
        def emit(self, event: TransportEvent) -> None:
            captured.append(event)

    emit.reset()
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        assert asyncio.run(client.list_tools())[0]["name"] == "web-search"
        assert captured == []
        emit.bind_config(TransportObservabilityConfig(present=True, defaults=ProtocolTransportConfig()))
        emit.bind_sink(Sink())
        assert asyncio.run(client.list_tools())[0]["name"] == "web-search"
    emit.reset()
    kinds = [e.kind for e in captured if e.protocol == PROTOCOL]
    assert kinds == [DISCOVERY]
    session.list_tools.assert_awaited_once()


def test_list_tools_stamps_the_listing_agent_not_the_mcp_user() -> None:
    """Mutation: emit_tools_discovered uses user= (often empty) so SCOPE agent.name is missing."""
    from mas.runtime.schema.transport import TransportEvent
    from mas.runtime.transport import emit
    from mas.runtime.transport.config import ProtocolTransportConfig, TransportObservabilityConfig

    client = MCPClient(url="http://127.0.0.1:9001/mcp")
    session = _Session(list_pages=[_ListResult([_Dump({"name": "web-search"})])])
    captured: list[TransportEvent] = []

    class Sink:
        def emit(self, event: TransportEvent) -> None:
            captured.append(event)

    emit.reset()
    emit.bind_config(TransportObservabilityConfig(present=True, defaults=ProtocolTransportConfig()))
    emit.bind_sink(Sink())
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        asyncio.run(client.list_tools(user="", agent_id="worker"))
    emit.reset()
    assert captured[0].agent_id == "worker"
    assert captured[0].attributes.get("source") == "worker"


def test_run_sync_from_running_event_loop() -> None:
    from library_ioa.plugins.mcp.client.wrapper import run_sync

    async def _inner() -> int:
        async def _value() -> int:
            return 7

        return run_sync(_value())

    assert asyncio.run(_inner()) == 7


@pytest.mark.parametrize("sticky", [False, True])
def test_refused_session_setup_is_attempted_once_per_tool_call(sticky):
    """The generic client does not retry a refused setup inside one call: one attempt, then the error."""
    client = MCPClient(url="http://127.0.0.1:9001/mcp")
    attempts: list[int] = []

    async def refuse(_stack):
        attempts.append(1)
        raise PermissionError("setup refused")

    client._enter_session = refuse  # type: ignore[method-assign]

    async def scenario():
        if sticky:
            await client.connect()
        with pytest.raises(PermissionError):
            await client.call_tool("t", {})
        assert len(attempts) == 1

    asyncio.run(scenario())


def test_list_without_server_ttl_expires_after_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """A server that never says tools changed does not pin the list forever."""
    from library_ioa.plugins.mcp import client as client_mod

    clock = [1000.0]
    monkeypatch.setattr(client_mod.time, "monotonic", lambda: clock[0])
    client = MCPClient(url="http://127.0.0.1:9001/mcp")
    session = _Session(list_pages=[_real_result(), _real_result()])
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        asyncio.run(client.list_tools())
        clock[0] += client_mod.DEFAULT_LIST_TTL_MS / 1000 - 1
        asyncio.run(client.list_tools())
        assert session.list_tools.await_count == 1
        clock[0] += 2
        asyncio.run(client.list_tools())
    assert session.list_tools.await_count == 2


def test_a_listing_in_flight_when_the_list_changes_is_not_stored() -> None:
    from mcp.types import ToolListChangedNotification

    client = MCPClient(url="http://127.0.0.1:9001/mcp")
    session = _Session(list_pages=[_real_result(), _real_result()])
    real = session.list_tools.side_effect

    async def changed_while_listing(params=None):
        await client._on_session_message(ToolListChangedNotification())
        return real(params)

    session.list_tools = AsyncMock(side_effect=changed_while_listing)
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        asyncio.run(client.list_tools())
        asyncio.run(client.list_tools())
    assert session.list_tools.await_count == 2


def test_a_protocol_error_on_a_call_drops_the_cached_list() -> None:
    from library_ioa.plugins.mcp.client import McpError

    client = MCPClient(url="http://127.0.0.1:9001/mcp")
    session = _Session(list_pages=[_real_result(), _real_result()])
    session.call_tool = AsyncMock(side_effect=McpError(-32602, "unknown tool"))
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        asyncio.run(client.list_tools())
        with pytest.raises(McpError):
            asyncio.run(client.call_tool("a", {}))
        asyncio.run(client.list_tools())
    assert session.list_tools.await_count == 2


def test_a_caller_cannot_change_the_cached_list() -> None:
    client = MCPClient(url="http://127.0.0.1:9001/mcp")
    session = _Session(list_pages=[_real_result()])
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        first = asyncio.run(client.list_tools())
        first[0]["name"] = "changed"
        first.clear()
        assert [t["name"] for t in asyncio.run(client.list_tools())] == ["a"]


def test_an_auth_refusal_on_a_call_does_not_drop_the_cached_list() -> None:
    from library_ioa.plugins.mcp.client import McpError

    client = MCPClient(url="http://127.0.0.1:9001/mcp")
    session = _Session(list_pages=[_real_result(), _real_result()])
    session.call_tool = AsyncMock(side_effect=McpError(-32001, "denied"))
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        asyncio.run(client.list_tools())
        for _ in range(3):
            with pytest.raises(McpError):
                asyncio.run(client.call_tool("a", {}))
        asyncio.run(client.list_tools())
    assert session.list_tools.await_count == 1


def test_bad_arguments_do_not_drop_the_cached_list() -> None:
    from library_ioa.plugins.mcp.client import McpError

    client = MCPClient(url="http://127.0.0.1:9001/mcp")
    session = _Session(list_pages=[_real_result(), _real_result()])
    session.call_tool = AsyncMock(side_effect=McpError(-32602, "field 'x' is required"))
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        asyncio.run(client.list_tools())
        with pytest.raises(McpError):
            asyncio.run(client.call_tool("a", {}))
        asyncio.run(client.list_tools())
    assert session.list_tools.await_count == 1


def test_a_stale_listing_is_not_announced() -> None:
    from mcp.types import ToolListChangedNotification

    client = MCPClient(url="http://127.0.0.1:9001/mcp")
    session = _Session(list_pages=[_real_result(), _real_result()])
    real = session.list_tools.side_effect

    async def changed_while_listing(params=None):
        await client._on_session_message(ToolListChangedNotification())
        return real(params)

    session.list_tools = AsyncMock(side_effect=changed_while_listing)
    announced: list[int] = []
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
        patch.object(client, "_emit_listed", lambda *a, **k: announced.append(1)),
    ):
        asyncio.run(client.list_tools())
    assert announced == []


def test_the_session_is_built_with_the_list_changed_handler() -> None:
    client = MCPClient(url="http://127.0.0.1:9001/mcp")
    with patch("library_ioa.plugins.mcp.client.ClientSession") as factory:
        client._client_session(object(), object())
    assert factory.call_args.kwargs["message_handler"] == client._on_session_message


def test_expired_entries_of_other_users_are_evicted(monkeypatch: pytest.MonkeyPatch) -> None:
    from library_ioa.plugins.mcp import client as client_mod

    clock = [0.0]
    monkeypatch.setattr(client_mod.time, "monotonic", lambda: clock[0])
    client = MCPClient(url="http://127.0.0.1:9001/mcp", cache_scope="private", cache_ttl_ms=1000)
    client._store_list("alice", [{"name": "a"}])
    clock[0] = 5.0
    client._store_list("bob", [{"name": "b"}])
    assert list(client._list_cache) == ["bob"]


def test_method_not_found_on_a_call_drops_the_cached_list() -> None:
    from library_ioa.plugins.mcp.client import McpError

    client = MCPClient(url="http://127.0.0.1:9001/mcp")
    session = _Session(list_pages=[_real_result(), _real_result()])
    session.call_tool = AsyncMock(side_effect=McpError(-32601, "method not found"))
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        asyncio.run(client.list_tools())
        with pytest.raises(McpError):
            asyncio.run(client.call_tool("a", {}))
        asyncio.run(client.list_tools())
    assert session.list_tools.await_count == 2
