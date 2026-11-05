#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import asyncio
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


def test_connect_disconnect_are_session_lifecycle_noops() -> None:
    client = MCPClient(url="http://127.0.0.1:9001/mcp")
    assert asyncio.run(client.connect()) is None
    assert asyncio.run(client.disconnect()) is None


def test_wrapper_delegates_to_client() -> None:
    inner = MCPClient(url="http://127.0.0.1:9001/mcp")
    wrapper = MCPClientWrapper(inner)
    session = _Session(list_pages=[_ListResult([_Dump({"name": "t"})])])
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session),
    ):
        assert asyncio.run(wrapper.connect()) is None
        tools = asyncio.run(wrapper.list_tools())
        payload = asyncio.run(wrapper.call_tool("t", {}))
        assert asyncio.run(wrapper.disconnect()) is None
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


def test_run_sync_from_running_event_loop() -> None:
    from library_ioa.plugins.mcp.client.wrapper import run_sync

    async def _inner() -> int:
        async def _value() -> int:
            return 7

        return run_sync(_value())

    assert asyncio.run(_inner()) == 7
