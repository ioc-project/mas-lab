#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The MCP client is as it was when no session hook is given; a hook takes part in setup and nothing else changes."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from library_ioa.plugins.mcp.client import MCPClient
from mas.library.standard.lib.stack.session import SessionHook


class _Initialized:
    def model_dump(self, **_: object) -> dict:
        return {"protocolVersion": "2025-11-25", "capabilities": {}}


class _Session:
    def __init__(self) -> None:
        self.initialize = AsyncMock(return_value=_Initialized())
        self.list_tools = AsyncMock(return_value=type("R", (), {"tools": [], "next_cursor": None, "ttl_ms": None})())
        self.call_tool = AsyncMock(return_value=type("D", (), {"model_dump": lambda self, **_: {"content": []}})())

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _Transport:
    async def __aenter__(self) -> tuple[object, object]:
        return object(), object()

    async def __aexit__(self, *exc: object) -> bool:
        return False


def test_with_no_hook_the_session_and_the_requests_are_the_sdk_defaults() -> None:
    """Kills: a default client that wraps requests in ``_meta`` or opens an advertising session for nobody."""
    client = MCPClient(url="http://127.0.0.1:9001/mcp")
    session = _Session()
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.ClientSession", return_value=session) as plain,
        patch("library_ioa.plugins.mcp.client.AdvertisingSession") as advertising,
    ):
        asyncio.run(client.list_tools())
        asyncio.run(client.call_tool("t", {"a": 1}))
    assert plain.call_count == 2 and advertising.call_count == 0
    assert session.list_tools.call_args.kwargs["params"] is None
    assert "meta" not in session.call_tool.call_args.kwargs
    assert session.initialize.await_count == 2


def test_a_hook_advertises_before_initialize_and_settles_the_session_meta() -> None:
    order: list[str] = []

    class Hook(SessionHook):
        async def begin(self, sections, task):
            order.append(f"begin:{task}")
            return {"mcp.capabilities.experimental": {"x/y": {"v": 1}}}

        async def established(self, session):
            order.append("established")
            return {"x/proof": "p"}

    client = MCPClient(url="http://127.0.0.1:9001/mcp", session_hooks=[Hook()])
    session = _Session()
    session.initialize = AsyncMock(side_effect=lambda: order.append("initialize") or _Initialized())
    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.AdvertisingSession", return_value=session) as advertising,
    ):
        asyncio.run(client.call_tool("t", {}))
    assert order == ["begin:initialize", "initialize", "established"]
    assert advertising.call_args.kwargs["experimental"] == {"x/y": {"v": 1}}
    assert session.call_tool.call_args.kwargs["meta"] == {"x/proof": "p"}


def test_an_error_a_hook_raises_is_what_the_caller_gets_not_a_task_group_wrapper() -> None:
    class Refusing(SessionHook):
        async def established(self, session):
            raise PermissionError("identity: refused")

    client = MCPClient(url="http://127.0.0.1:9001/mcp", session_hooks=[Refusing()])

    class _Group(_Session):
        async def __aexit__(self, *exc: object) -> bool:
            if exc[0] is not None:
                raise BaseExceptionGroup("unhandled errors in a TaskGroup", [exc[1]])
            return False

    with (
        patch("library_ioa.plugins.mcp.client.streamable_http_client", return_value=_Transport()),
        patch("library_ioa.plugins.mcp.client.AdvertisingSession", return_value=_Group()),
    ):
        try:
            asyncio.run(client.list_tools())
        except BaseException as exc:  # noqa: BLE001
            raised = exc
    assert isinstance(raised, PermissionError) and str(raised) == "identity: refused"
