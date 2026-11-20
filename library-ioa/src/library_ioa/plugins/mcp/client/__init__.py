#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import asyncio
import copy
import logging
import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from mcp.types import PaginatedRequestParams, ToolListChangedNotification
from pydantic import BaseModel

try:  # mcp 2.x
    from mcp.shared.exceptions import MCPError as McpError
except ImportError:  # pragma: no cover - mcp 1.x
    from mcp.shared.exceptions import McpError

logger = logging.getLogger(__name__)

_HTTP_TRANSPORTS = {"streamable-http", "http"}
_SSE_TRANSPORTS = {"sse"}

# A listing the server gave no ``ttlMs`` for is kept this long: a safety net for servers that never send
# ``notifications/tools/list_changed`` and for sessions that are not held open to receive it.
DEFAULT_LIST_TTL_MS = 60_000


_CONNECTION_CLOSED = -32000  # mcp.types.CONNECTION_CLOSED


def _error_code(exc: BaseException) -> Any:
    code = getattr(exc, "code", None)
    if code is None:
        code = getattr(getattr(exc, "error", None), "code", None)
    return code


def _connection_closed(exc: BaseException) -> bool:
    return _error_code(exc) == _CONNECTION_CLOSED


_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602


def _names_missing_tool(exc: BaseException) -> bool:
    """True when the server says the tool is not (or no longer) there.

    "Invalid params" is also how servers answer a model's bad arguments, which says nothing about the
    list, so it counts only when the message names an unknown tool.
    """
    code = _error_code(exc)
    if code == _METHOD_NOT_FOUND:
        return True
    text = str(exc).lower()
    return code == _INVALID_PARAMS and ("unknown tool" in text or "tool not found" in text or "no such tool" in text)


def _streams_from_transport(transport: Any) -> tuple[Any, Any]:
    """streamable_http_client / stdio_client / sse_client yield (read, write) or (read, write, extra)."""
    if isinstance(transport, (tuple, list)) and len(transport) >= 2:
        return transport[0], transport[1]
    raise TypeError(f"unexpected MCP transport yield: {type(transport)!r}")


def _announced_ttl_ms(result: Any) -> int | None:
    """The ``ttlMs`` the server sent, or ``None`` when it sent none.

    The SDK's result model fills an absent ``ttlMs`` with ``0``, which reads as
    "do not cache". Only a value the server set counts; without one the client
    keeps the listing for ``DEFAULT_LIST_TTL_MS``.
    """
    if isinstance(result, BaseModel):
        return result.ttl_ms if "ttl_ms" in result.model_fields_set else None
    return result.ttl_ms


def _current_task() -> asyncio.Task | None:
    """The running task, or ``None`` once the event loop is gone (a session closed late)."""
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


class MCPClient:
    """MCP client session backed by the official SDK.

    Call ``connect`` to keep one session for later list/call on the same
    event loop. Without ``connect``, each list/call opens and closes the
    transport in that task so a one-shot ``asyncio.run`` does not outlive
    the loop. The sync wrapper holds a dedicated loop and calls ``connect``
    so the provider reuses initialize.

    ``cache_ttl_ms`` / ``cache_scope`` control the client's ``tools/list``
    cache. Omit TTL to use the ``ttlMs`` the server sends, or
    ``DEFAULT_LIST_TTL_MS`` when it sends none; a ``notifications/tools/list_changed``,
    ``invalidate_list_cache`` or a tool call that fails with a protocol error drops
    the list earlier. ``0`` disables the cache. ``private`` keys the cache by user;
    use it when the tools a server lists differ by caller.
    """

    def __init__(
        self,
        *,
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | Path | None = None,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        transport: str | None = None,
        follow_pagination: bool = True,
        cache_ttl_ms: int | None = None,
        cache_scope: str | None = None,
        http_client: Any = None,
    ) -> None:
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.cwd = cwd
        self.url = url
        self.headers = dict(headers or {})
        self.timeout = timeout
        self.http_client = http_client
        self.follow_pagination = follow_pagination
        self.cache_ttl_ms = cache_ttl_ms
        self.cache_scope = cache_scope if cache_scope in {"public", "private"} else None
        self._list_cache: dict[str, tuple[float, list[dict[str, Any]], int]] = {}
        self._discovery_emitted: set[str] = set()
        self._sticky = False
        self._owner: asyncio.Task | None = None
        self._stop: asyncio.Event | None = None
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        self._session: ClientSession | None = None
        self._list_generation = 0
        self._loop_id: int | None = None
        if transport:
            self.transport = transport
        elif url:
            self.transport = "streamable-http"
        else:
            self.transport = "stdio"

    def invalidate_list_cache(self) -> None:
        # A listing already on the wire must not be stored after this: it may predate the change.
        self._list_generation += 1
        self._list_cache.clear()
        self._discovery_emitted.clear()

    async def _on_session_message(self, message: Any) -> None:
        """A server that announces a changed tool list makes the next listing go to the wire."""
        if isinstance(message, ToolListChangedNotification):
            self.invalidate_list_cache()

    def _client_session(self, read_stream: Any, write_stream: Any) -> ClientSession:
        return ClientSession(read_stream, write_stream, message_handler=self._on_session_message)

    def _cache_key(self, user: str) -> str:
        if self.cache_scope == "private":
            return user
        return ""

    def _cached_list(self, user: str) -> list[dict[str, Any]] | None:
        if self.cache_ttl_ms == 0:
            return None
        packed = self._list_cache.get(self._cache_key(user))
        if packed is None:
            return None
        stored_at, payload, ttl_ms = packed
        age_ms = (time.monotonic() - stored_at) * 1000
        if age_ms >= ttl_ms:
            return None
        return payload

    def _store_list(
        self, user: str, payload: list[dict[str, Any]], *, server_ttl: int | None = None, generation: int | None = None
    ) -> None:
        if self.cache_ttl_ms == 0:
            return
        if generation is not None and generation != self._list_generation:
            return
        if self.cache_ttl_ms is not None:
            ttl = self.cache_ttl_ms
        elif server_ttl is not None:
            ttl = server_ttl
        else:
            ttl = DEFAULT_LIST_TTL_MS
        now = time.monotonic()
        for key in [k for k, (at, _p, ttl_ms) in self._list_cache.items() if (now - at) * 1000 >= ttl_ms]:
            del self._list_cache[key]  # expired entries of other users do not accumulate
        self._list_cache[self._cache_key(user)] = (now, payload, ttl)

    async def _enter_session(self, stack: AsyncExitStack) -> ClientSession:
        if self.transport in _SSE_TRANSPORTS:
            if not self.url:
                raise ValueError("MCP SSE client requires a url")
            sse_kw: dict[str, Any] = {}
            if self.headers:
                sse_kw["headers"] = self.headers
            transport = await stack.enter_async_context(sse_client(self.url, **sse_kw))
            read_stream, write_stream = _streams_from_transport(transport)
            session = await stack.enter_async_context(self._client_session(read_stream, write_stream))
            await session.initialize()
            return session

        if self.transport in _HTTP_TRANSPORTS or (self.url and self.transport != "stdio"):
            if not self.url:
                raise ValueError("MCP HTTP client requires a url")
            # Connection timeout stays on the SDK client (30s / 300s read).
            # spec timeout is call_tool read_timeout_seconds, not the HTTP budget.
            http_client = self.http_client() if callable(self.http_client) else self.http_client
            if http_client is not None:
                transport = await stack.enter_async_context(streamable_http_client(self.url, http_client=http_client))
            elif self.headers:
                http_client = await stack.enter_async_context(create_mcp_http_client(headers=self.headers))
                transport = await stack.enter_async_context(streamable_http_client(self.url, http_client=http_client))
            else:
                transport = await stack.enter_async_context(streamable_http_client(self.url))
            read_stream, write_stream = _streams_from_transport(transport)
            session = await stack.enter_async_context(self._client_session(read_stream, write_stream))
            await session.initialize()
            return session

        if not self.command:
            raise ValueError("MCP client requires a command to start the server")

        params = StdioServerParameters(command=self.command, args=self.args, env=self.env or None, cwd=self.cwd)
        transport = await stack.enter_async_context(stdio_client(params))
        read_stream, write_stream = _streams_from_transport(transport)
        session = await stack.enter_async_context(self._client_session(read_stream, write_stream))
        await session.initialize()
        return session

    @asynccontextmanager
    async def _ephemeral_session(self) -> AsyncIterator[ClientSession]:
        async with AsyncExitStack() as stack:
            yield await self._enter_session(stack)

    async def _hold(self, ready: asyncio.Future, stop: asyncio.Event) -> None:
        """Own the transport. Enter and exit in this one task.

        anyio cancel scopes must be exited by the task that entered them, so a
        later ``disconnect`` from another task only sets ``stop``.
        """
        try:
            async with AsyncExitStack() as stack:
                session = await self._enter_session(stack)
                if not ready.done():
                    ready.set_result(session)
                await stop.wait()
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
            elif not isinstance(exc, asyncio.CancelledError):
                logger.warning("MCP session ended: %s", exc)
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            current = _current_task()
            if current is None or self._owner is current:
                self._session = None
                self._owner = None
                self._stop = None

    async def _close_held(self) -> None:
        if self._session is not None or self._owner is not None:
            self.invalidate_list_cache()
        owner, stop = self._owner, self._stop
        self._session = None
        self._owner = None
        self._stop = None
        self._loop_id = None
        if owner is None or stop is None:
            return
        try:
            same_loop = owner.get_loop() is asyncio.get_running_loop()
        except RuntimeError:
            same_loop = False
        if not same_loop or owner.done():
            return
        stop.set()
        await asyncio.gather(owner, return_exceptions=True)

    async def _ensure_session(self) -> ClientSession:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        async with self._lock:
            owner = self._owner
            if self._session is not None and self._loop_id == id(loop) and owner is not None and not owner.done():
                return self._session
            await self._close_held()
            ready: asyncio.Future = loop.create_future()
            stop = asyncio.Event()
            owner = loop.create_task(self._hold(ready, stop))
            self._owner, self._stop = owner, stop
            try:
                session = await ready
            except BaseException:
                await asyncio.gather(owner, return_exceptions=True)
                raise
            self._session = session
            self._loop_id = id(loop)
            return session

    @asynccontextmanager
    async def _use_session(self) -> AsyncIterator[ClientSession]:
        if self._sticky:
            session = await self._ensure_session()
            try:
                yield session
            except (asyncio.CancelledError, TimeoutError):
                raise
            except Exception as exc:
                # A tool or protocol error leaves the session usable. A closed
                # or broken transport does not: drop it so the next call reconnects.
                if not isinstance(exc, McpError) or _connection_closed(exc):
                    await self._close_held()
                raise
            return
        async with self._ephemeral_session() as session:
            yield session

    async def connect(self) -> None:
        """Keep one session for later list/call on this event loop."""
        self._sticky = True

    async def disconnect(self) -> None:
        """Close a held session. No-op when list/call were one-shot."""
        self._sticky = False
        await self._close_held()

    def _emit_listed(self, tools: list[dict[str, Any]], user: str, *, agent_id: str = "") -> None:
        from library_ioa.plugins.mcp.hooks import emit_tools_discovered, hooks_enabled

        if not hooks_enabled():
            return
        key = self._cache_key(user)
        if key in self._discovery_emitted:
            return
        emit_tools_discovered(tools, agent_id=agent_id or user)
        self._discovery_emitted.add(key)

    async def list_tools(self, *, user: str = "", agent_id: str = "") -> list[dict[str, Any]]:
        """Return the advertised set, following MCP ``nextCursor`` when enabled."""
        cached = self._cached_list(user)
        if cached is not None:
            self._emit_listed(cached, user, agent_id=agent_id)
            return copy.deepcopy(cached)
        async with self._use_session() as session:
            # Taken once the session is in hand: opening it may itself invalidate (a reconnect).
            generation = self._list_generation
            collected: list[dict[str, Any]] = []
            cursor: str | None = None
            seen: set[str] = set()
            server_ttl: int | None = None
            while True:
                params = PaginatedRequestParams(cursor=cursor) if cursor else None
                result = await session.list_tools(params=params)
                collected.extend(tool.model_dump(mode="json", by_alias=True) for tool in result.tools)
                page_ttl = _announced_ttl_ms(result)
                if page_ttl is not None:
                    page_ttl_i = int(page_ttl)
                    server_ttl = page_ttl_i if server_ttl is None else min(server_ttl, page_ttl_i)
                if not self.follow_pagination:
                    break
                cursor = getattr(result, "next_cursor", None)
                if not cursor or cursor in seen:
                    break
                seen.add(cursor)
            self._store_list(user, collected, server_ttl=server_ttl, generation=generation)
            if generation == self._list_generation:  # a listing that predates a change is not announced
                self._emit_listed(collected, user, agent_id=agent_id)
            return copy.deepcopy(collected)

    async def call_tool(self, tool_name: str, arguments: dict[str, Any], **options: Any) -> dict[str, Any]:
        call_kwargs: dict[str, Any] = {}
        timeout = options.get("timeout_seconds", self.timeout)
        if timeout is not None:
            call_kwargs["read_timeout_seconds"] = timeout
        for src, dest in (
            ("progress_callback", "progress_callback"),
            ("meta", "meta"),
            ("input_responses", "input_responses"),
            ("request_state", "request_state"),
            ("allow_input_required", "allow_input_required"),
            ("allow_claimed", "allow_claimed"),
        ):
            if src in options and options[src] is not None:
                call_kwargs[dest] = options[src]
        from library_ioa.plugins.mcp.hooks import (
            emit_call_completed,
            emit_call_error,
            emit_call_sent,
            hooks_enabled,
            new_call_key,
        )

        announce = hooks_enabled()
        key = new_call_key(tool_name) if announce else ""
        if announce:
            emit_call_sent(tool=tool_name, source="", target="", key=key)
        try:
            async with self._use_session() as session:
                result = await session.call_tool(tool_name, arguments, **call_kwargs)
        except BaseException as exc:
            if isinstance(exc, McpError) and _names_missing_tool(exc):
                # The tool may be gone or renamed: list again before the next routing decision. Other
                # refusals (auth, rate limit) say nothing about the list and must not add a listing per call.
                self.invalidate_list_cache()
            if announce:
                emit_call_error(side="client", tool=tool_name, key=key, error_type=type(exc).__name__)
            raise
        if announce:
            emit_call_completed(side="client", tool=tool_name, source="", target="", key=key)
        return result.model_dump(mode="json", by_alias=True)
