#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import asyncio
import copy
import logging
import time
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

import httpx2
from mas.library.standard.lib.stack import SESSION_TOKEN, SessionHook, SessionInfo
from mas.runtime.boundary.agentcomm.protocol import LayerRefusal
from mas.runtime.engine.tool_dispatch import IN_FLIGHT_TOOL
from mas.runtime.secrets.transport import httpx_client_kwargs_for
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import MCP_DEFAULT_SSE_READ_TIMEOUT, MCP_DEFAULT_TIMEOUT
from mcp.types import PaginatedRequestParams, ToolListChangedNotification
from pydantic import BaseModel, ConfigDict

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
#: A scope whose session setup was refused this many times in a row (the first attempt and one retry) fails closed...
#: The operation the MCP SDK itself requests while a tool call runs (``validate_tool_result`` lists the tools).
_SDK_REQUEST = "tools/list"
_MAX_REFUSED_SETUPS = 2
#: ... for this many seconds, with the refusal it got, without contacting the peer again. A tool call in flight keeps
#: its own memory for as long as it runs, whatever this window says.
_REFUSED_SETUP_TTL_S = 10.0
#: At most this many scopes (and, apart, this many calls) are remembered.
_MAX_REFUSED_SCOPES = 64
_MAX_REFUSED_CALLS = 256


class _Closed(Exception):
    """A call whose session setup the peer refused ``_MAX_REFUSED_SETUPS`` times: it fails without asking again."""

    def __init__(self, refusal: BaseException) -> None:
        super().__init__(str(refusal))
        self.refusal = refusal


def _call_in_flight() -> str:
    """The kernel's id of the tool call running here, else ``""``."""
    tool = IN_FLIGHT_TOOL.get()
    return str(tool.call) if tool is not None and tool.call is not None else ""


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


def first_error(exc: BaseException) -> BaseException:
    """The error an exception group stands for: its first leaf that is not a cancellation.

    The SDK opens a transport inside a task group, so a refused connection leaves it as an ``ExceptionGroup``
    whose text ("unhandled errors in a TaskGroup") says nothing. The caller wants the real error.
    """
    if not isinstance(exc, BaseExceptionGroup):
        return exc
    leaves: list[BaseException] = []
    pending: list[BaseException] = [exc]
    while pending:
        item = pending.pop(0)
        if isinstance(item, BaseExceptionGroup):
            pending[:0] = list(item.exceptions)
        else:
            leaves.append(item)
    for leaf in leaves:
        if not isinstance(leaf, (Exception, asyncio.CancelledError)):
            return leaf  # KeyboardInterrupt, SystemExit: never hidden behind an ordinary error
    for leaf in leaves:
        if isinstance(leaf, Exception):
            return leaf
    return leaves[0] if leaves else exc


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


class _RawParams(types.RequestParams):
    """The params of a request the SDK has no type for: whatever the hook sends, as it is."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class _RawRequest(types.Request[_RawParams, str]):
    pass


class _RawResult(types.Result):
    """A result the SDK has no type for: every field the peer sent."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


#: The ``Evidence.advertise`` sections this client can carry: the capabilities of the client's declaration.
MCP_SECTIONS = frozenset({"mcp.capabilities.experimental", "mcp.capabilities.extensions"})


class AdvertisingSession(ClientSession):
    """A ``ClientSession`` that can advertise ``experimental`` capabilities too.

    The SDK takes ``extensions`` but has no argument for ``experimental``; overriding ``_build_capabilities``
    puts the declaration in the ``initialize`` request. Used only when a session hook asks to advertise.
    """

    def __init__(self, *args: Any, experimental: Mapping[str, Mapping[str, Any]] | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._experimental = {str(k): dict(v) for k, v in (experimental or {}).items()}

    def _build_capabilities(self, version: str) -> types.ClientCapabilities:
        capabilities = super()._build_capabilities(version)
        if not self._experimental:
            return capabilities
        return capabilities.model_copy(update={"experimental": self._experimental})


class _McpSession(SessionInfo):
    """What a session hook sees of an open MCP session: the ``initialize`` result, and one more request."""

    def __init__(self, session: ClientSession, initialized: Mapping[str, Any]) -> None:
        self._session = session
        self.peer = initialized

    async def request(
        self, method: str, params: Mapping[str, Any], *, data_key: str | None = None
    ) -> Mapping[str, Any]:
        del data_key  # a JSON-RPC session has no data part
        raw = _RawRequest(method=method, params=_RawParams(**dict(params)))
        result = await self._session.send_request(raw, _RawResult)
        return result.model_dump(by_alias=True, mode="json", exclude_none=True)


def _peer_declaration(result: dict[str, Any]) -> dict[str, Any]:
    """What the server declared in its ``initialize`` result, as one mapping for a session hook.

    The result itself, plus the fields of every namespaced object the server put under
    ``capabilities.experimental``, ``capabilities.extensions`` or ``_meta`` (an answer to a capability the client
    advertised), so a server that answers an advertised key shows its fields at the top. A name the result
    already has is not overwritten.
    """
    peer = dict(result)
    capabilities = result.get("capabilities") or {}
    for source in (capabilities.get("experimental"), capabilities.get("extensions"), result.get("_meta")):
        for value in (source or {}).values():
            if isinstance(value, dict):
                for key, item in value.items():
                    peer.setdefault(key, item)
    return peer


def _refusal_in(exc: BaseException) -> LayerRefusal | None:
    """The first refusal a layer raised that a task-group failure holds (the SDK wraps what a transport task raised),
    else ``None``."""
    if isinstance(exc, LayerRefusal):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for inner in exc.exceptions:
            found = _refusal_in(inner)
            if found is not None:
                return found
    return None


def _hook_error_of(group: BaseException, hook_error: BaseException | None) -> BaseException:
    """What a task-group failure stands for: the error a session hook raised, when one did, else the group itself."""
    return hook_error if hook_error is not None and isinstance(group, BaseExceptionGroup) else group


#: What the SDK client reports for an HTTP 404 on a request that names a session (and what the MAS MCP gate says with
#: that 404): the one signal that the server does not hold the session, so the request was not served.
_SESSION_GONE = frozenset({"Session terminated", "Session not found"})
_INVALID_REQUEST = -32600


def _session_gone(exc: BaseException) -> bool:
    """Is ``exc`` exactly the signal that the server does not hold the session (a 404 on the session)?

    Not a match on text: a tool that fails with a message about some session (``payment session not found at bank``)
    was served, and replaying it would run it twice. A tool's own failure arrives as a result with ``isError``, which
    never raises, so it is never retried.
    """
    return isinstance(exc, McpError) and exc.code == _INVALID_REQUEST and exc.message in _SESSION_GONE


def _current_task() -> asyncio.Task | None:
    """The running task, or ``None`` once the event loop is gone (a session closed late)."""
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


class _Held:
    """One held session: the task that owns its transport, who is using it, and whether it has been retired.

    A retired session is no longer handed to new calls; its transport is closed when the last call using it is done,
    so a call under one principal never has its session closed by another principal's call.
    """

    def __init__(self, scope: str, loop_id: int) -> None:
        self.scope = scope
        self.loop_id = loop_id
        self.session: ClientSession | None = None
        self.owner: asyncio.Task | None = None
        self.stop: asyncio.Event | None = None
        self.users = 0
        self.retired = False
        self.token = object()  # names this session to a hook, whichever call is using it


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
        env: dict[str, Any] | None = None,
        cwd: str | Path | None = None,
        url: str | None = None,
        headers: dict[str, Any] | None = None,
        timeout: float | None = None,
        transport: str | None = None,
        follow_pagination: bool = True,
        cache_ttl_ms: int | None = None,
        cache_scope: str | None = None,
        http_client: Any = None,
        session_hooks: list[SessionHook] | None = None,
    ) -> None:
        # a layer below may take part in session setup; with no hook the client is exactly as it was
        self.session_hooks = list(session_hooks or [])
        for hook in self.session_hooks:
            hook.on_invalidate(self._invalidate)
        # the error a hook raised while setting a session up, by the exit stack of that setup: two setups at once (two
        # scopes) never read each other's
        self._hook_errors: weakref.WeakKeyDictionary[Any, BaseException] = weakref.WeakKeyDictionary()
        self._session_meta: weakref.WeakKeyDictionary[Any, dict[str, Any]] = weakref.WeakKeyDictionary()
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.cwd = cwd
        self.url = url
        from mas.runtime.secrets.ref import check_http_header_secrets

        self.headers = dict(headers or {})
        check_http_header_secrets(self.headers)
        self.timeout = timeout
        self.http_client = http_client
        self.follow_pagination = follow_pagination
        self.cache_ttl_ms = cache_ttl_ms
        self.cache_scope = cache_scope if cache_scope in {"public", "private"} else None
        self._list_cache: dict[str, tuple[float, list[dict[str, Any]], int]] = {}
        self._discovery_emitted: set[str] = set()
        self._sticky = False
        # one held session per scope (per acting principal); see ``_Held``
        self._held: dict[str, _Held] = {}
        #: scope -> (refused setups in a row, when the last was, its refusal): see ``_MAX_REFUSED_SETUPS``
        self._refused_setups: dict[str, tuple[int, float, BaseException]] = {}
        #: (scope, tool call) -> (refused setups, the last refusal): counted for as long as the call runs, no window
        self._refused_calls: dict[str, tuple[int, BaseException]] = {}
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        self._list_generation = 0
        if transport:
            self.transport = transport
        elif url:
            self.transport = "streamable-http"
        else:
            self.transport = "stdio"

    def _http_client(self, headers: Any = None, timeout: Any = None, auth: Any = None) -> Any:
        """The SDK's client (its timeouts), proxy settings ignored for a loopback url. A server's HTTP error
        is handled as the SDK reports it: its replies are its own output."""
        if timeout is None:
            timeout = httpx2.Timeout(MCP_DEFAULT_TIMEOUT, read=MCP_DEFAULT_SSE_READ_TIMEOUT)
        kwargs: dict[str, Any] = {"timeout": timeout, **httpx_client_kwargs_for(self.url)}
        if headers is not None:
            kwargs["headers"] = headers
        if auth is not None:
            kwargs["auth"] = auth
        return httpx2.AsyncClient(**kwargs)

    def _revealed_headers(self) -> dict[str, str]:
        from mas.runtime.secrets.transport import PlainHttpRefused, reveal_headers_for

        try:
            return reveal_headers_for(self.url, self.headers, what="secret headers")
        except PlainHttpRefused as exc:
            raise ValueError(f"mcp client: {exc}") from None

    def _stdio_env(self) -> dict[str, str]:
        from mas.runtime.secrets.resolver import reveal_mapping

        return reveal_mapping(self.env)

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

    def _scope(self) -> str:
        """The scope of a call made now: a held session serves calls of one scope only."""
        return "\x1f".join(hook.scope() for hook in self.session_hooks)

    async def _open_session(self, stack: AsyncExitStack, read_stream: Any, write_stream: Any) -> ClientSession:
        """Open a session and ``initialize`` it. Session hooks advertise first and settle the session after."""
        if not self.session_hooks:
            session = await stack.enter_async_context(self._client_session(read_stream, write_stream))
            await session.initialize()
            return session
        self._hook_errors.pop(stack, None)
        try:
            advertised: dict[str, Any] = {}
            for hook in self.session_hooks:
                for section, declaration in (await hook.begin(MCP_SECTIONS, "initialize")).items():
                    advertised.setdefault(section, {}).update(declaration)
            session = await stack.enter_async_context(
                AdvertisingSession(
                    read_stream,
                    write_stream,
                    extensions=advertised.get("mcp.capabilities.extensions") or None,
                    experimental=advertised.get("mcp.capabilities.experimental"),
                    message_handler=self._on_session_message,
                )
            )
            initialized = await session.initialize()
            peer = _peer_declaration(initialized.model_dump(by_alias=True, mode="json", exclude_none=True))
            meta: dict[str, Any] = {}
            for hook in self.session_hooks:
                meta.update(await hook.established(_McpSession(session, peer)))
            self._session_meta[session] = meta
            return session
        except Exception as exc:
            # the SDK session runs in a task group, which wraps this; the caller wants the hook's value-free code
            self._hook_errors[stack] = exc
            raise

    async def _meta_for(self, session: Any, task: str, caller: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """The ``_meta`` a request of ``task`` carries: what the hooks settled for the session, then the caller's."""
        return {**self._session_meta.get(session, {}), **(caller or {})}

    async def _enter_session(self, stack: AsyncExitStack) -> ClientSession:
        if self.transport in _SSE_TRANSPORTS:
            if not self.url:
                raise ValueError("MCP SSE client requires a url")
            sse_kw: dict[str, Any] = {"httpx_client_factory": self._http_client}
            headers = self._revealed_headers()
            if headers:
                sse_kw["headers"] = headers
            transport = await stack.enter_async_context(sse_client(self.url, **sse_kw))
            read_stream, write_stream = _streams_from_transport(transport)
            return await self._open_session(stack, read_stream, write_stream)

        if self.transport in _HTTP_TRANSPORTS or (self.url and self.transport != "stdio"):
            if not self.url:
                raise ValueError("MCP HTTP client requires a url")
            # Connection timeout stays on the SDK client (30s / 300s read).
            # spec timeout is call_tool read_timeout_seconds, not the HTTP budget.
            http_client = self.http_client() if callable(self.http_client) else self.http_client
            if http_client is None:
                http_client = await stack.enter_async_context(
                    self._http_client(headers=self._revealed_headers() or None)
                )
            transport = await stack.enter_async_context(streamable_http_client(self.url, http_client=http_client))
            read_stream, write_stream = _streams_from_transport(transport)
            return await self._open_session(stack, read_stream, write_stream)

        if not self.command:
            raise ValueError("MCP client requires a command to start the server")

        params = StdioServerParameters(
            command=self.command, args=self.args, env=self._stdio_env() or None, cwd=self.cwd
        )
        transport = await stack.enter_async_context(stdio_client(params))
        read_stream, write_stream = _streams_from_transport(transport)
        return await self._open_session(stack, read_stream, write_stream)

    @asynccontextmanager
    async def _ephemeral_session(self) -> AsyncIterator[ClientSession]:
        body_error: BaseException | None = None
        stack = AsyncExitStack()
        try:
            async with stack:
                session = await self._enter_session(stack)
                try:
                    yield session
                except BaseException as exc:
                    body_error = exc
                    raise
        except BaseExceptionGroup as group:
            if _refusal_in(group) is not None and (
                body_error is None or isinstance(body_error, asyncio.CancelledError)
            ):
                # a layer refused in a transport task: the task group cancelled the body, so that cancellation is the
                # group's own doing, and the group (which holds the refusal) is what the caller sees
                raise _hook_error_of(group, self._hook_errors.get(stack)) from None
            if isinstance(body_error, BaseExceptionGroup) or (
                body_error is not None and not isinstance(body_error, Exception)
            ):
                # the caller's own group, or an interrupt or cancellation, is what the caller sees: the stack's
                # teardown group neither replaces it nor turns it into an ordinary error
                raise body_error from body_error.__cause__
            hook_error = _hook_error_of(group, self._hook_errors.get(stack))
            if hook_error is not group:
                raise hook_error from None
            unwrapped = first_error(group)
            if unwrapped.__cause__ is None:
                raise unwrapped from group
            raise unwrapped from unwrapped.__cause__

    async def _hold(self, held: _Held, ready: asyncio.Future) -> None:
        """Own the transport. Enter and exit in this one task.

        anyio cancel scopes must be exited by the task that entered them, so a
        later ``disconnect`` from another task only sets ``stop``.
        """
        assert held.stop is not None
        # this task sends the requests of every later call on the session: it inherited the tool call that opened it,
        # and must not stamp that call's operation and id on events of calls it did not make
        from mas.runtime.engine.tool_dispatch import IN_FLIGHT_TOOL

        IN_FLIGHT_TOOL.set(None)
        stack = AsyncExitStack()
        try:
            async with stack:
                session = await self._enter_session(stack)
                if not ready.done():
                    ready.set_result(session)
                await held.stop.wait()
        except BaseException as caught:
            exc = first_error(_hook_error_of(caught, self._hook_errors.get(stack)))
            if not ready.done():
                ready.set_exception(exc)
            elif not isinstance(exc, asyncio.CancelledError):
                logger.warning("MCP session ended: %s", exc)
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            current = _current_task()
            if current is None or held.owner is current:
                held.session = None
                self._forget(held)

    @property
    def held(self) -> bool:
        """Is a session held open right now?"""
        return any(held.session is not None for held in list(self._held.values()))

    def _forget(self, held: _Held) -> None:
        if self._held.get(held.scope) is held:
            del self._held[held.scope]

    def _invalidate(self) -> None:
        """A hook says what it settled is void (an identity was revoked): close the held sessions, from any thread.

        Each owner task closes its transport; the next call opens a new session, which the hook then refuses.
        """
        for held in list(self._held.values()):
            held.session = None
            if held.owner is not None and held.stop is not None:
                held.owner.get_loop().call_soon_threadsafe(held.stop.set)

    async def _stop_held(self, held: _Held) -> None:
        owner, stop = held.owner, held.stop
        held.session = None
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

    async def _retire(self, held: _Held) -> None:
        """No new call gets ``held``; its transport closes now when nobody uses it, else when the last user is done."""
        self._forget(held)
        held.retired = True
        if held.users == 0:
            await self._stop_held(held)

    async def _release(self, held: _Held) -> None:
        held.users -= 1
        if held.retired and held.users == 0:
            await self._stop_held(held)

    async def _close_held(self) -> None:
        if self._held:
            self.invalidate_list_cache()
        for held in list(self._held.values()):
            await self._retire(held)

    async def _acquire_held(self) -> _Held:
        """The held session of the current scope (opened when there is none), with the caller counted as a user."""
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        scope = self._scope()
        async with self._lock:
            # callers that queued here before the first refusal was noted are not each to try the peer again
            if (closed := self._closed_for(scope)) is not None:
                raise _Closed(closed)
            held = self._held.get(scope)
            if (
                held is not None
                and held.session is not None
                and held.loop_id == id(loop)
                and held.owner is not None
                and not held.owner.done()
            ):
                held.users += 1
                return held
            if held is not None:
                await self._retire(held)
            held = _Held(scope, id(loop))
            held.stop = asyncio.Event()
            ready: asyncio.Future = loop.create_future()
            reset = SESSION_TOKEN.set(held.token)  # the task that runs the session inherits it
            try:
                held.owner = loop.create_task(self._hold(held, ready))
            finally:
                SESSION_TOKEN.reset(reset)
            self._held[scope] = held
            try:
                held.session = await ready
            except BaseException:
                await asyncio.gather(held.owner, return_exceptions=True)
                self._forget(held)
                raise
            held.users = 1
            return held

    @asynccontextmanager
    async def _use_session_inner(self, token: list[object]) -> AsyncIterator[ClientSession]:
        if self._sticky:
            held = await self._acquire_held()
            assert held.session is not None
            token.append(held.token)
            try:
                yield held.session
            except (asyncio.CancelledError, TimeoutError):
                raise
            except Exception as exc:
                # A tool or protocol error leaves the session usable. A closed
                # or broken transport does not: drop it so the next call reconnects.
                if not isinstance(exc, McpError) or _connection_closed(exc):
                    await self._retire(held)
                raise
            finally:
                await self._release(held)
            return
        mine = object()
        token.append(mine)
        reset = SESSION_TOKEN.set(mine)  # a session made for this call is named to the hook before it is opened
        try:
            async with self._ephemeral_session() as session:
                yield session
        finally:
            SESSION_TOKEN.reset(reset)

    def _closed_for(self, scope: str) -> BaseException | None:
        """The refusal to give a call of ``scope`` without contacting the peer, once its session setup was refused
        ``_MAX_REFUSED_SETUPS`` times in a row: for the whole of a tool call in flight that was refused so, and for
        ``_REFUSED_SETUP_TTL_S`` after the last refusal for the scope."""
        call = _call_in_flight()
        kept = self._refused_calls.get(f"{scope}\x1e{call}") if call else None
        if kept is not None and kept[0] >= _MAX_REFUSED_SETUPS:
            return kept[1]
        found = self._refused_setups.get(scope)
        if found is None:
            return None
        count, at, refusal = found
        if time.monotonic() - at >= _REFUSED_SETUP_TTL_S:
            del self._refused_setups[scope]
            return None
        return refusal if count >= _MAX_REFUSED_SETUPS else None

    def _note_setup(self, scope: str, refusal: BaseException | None) -> None:
        """Count a refused setup of ``scope`` (``refusal``), or forget the count when a setup worked (``None``)."""
        if refusal is None:
            self._refused_setups.pop(scope, None)
            return
        count = self._refused_setups.get(scope, (0, 0.0, refusal))[0] + 1
        self._refused_setups.pop(scope, None)
        self._refused_setups[scope] = (count, time.monotonic(), refusal)
        while len(self._refused_setups) > _MAX_REFUSED_SCOPES:
            del self._refused_setups[next(iter(self._refused_setups))]
        call = _call_in_flight()
        if call:
            key = f"{scope}\x1e{call}"
            self._refused_calls[key] = (self._refused_calls.pop(key, (0, refusal))[0] + 1, refusal)
            while len(self._refused_calls) > _MAX_REFUSED_CALLS:
                del self._refused_calls[next(iter(self._refused_calls))]

    def _refusing(self, refusal: BaseException, task: str) -> BaseException:
        """``refusal`` as each hook says it for the call being refused now (its own trace and call)."""
        said = refusal
        for hook in self.session_hooks:
            said = hook.closed(said, task)
        return said if said is not refusal else LayerRefusal(str(refusal))  # a fresh one: never one exception twice

    @asynccontextmanager
    async def _use_session(self, task: str = "") -> AsyncIterator[ClientSession]:
        for hook in self.session_hooks:
            hook.take_refusal(task)  # one this operation was refused earlier is not this call's
        scope = self._scope()
        if self._sticky and (closed := self._closed_for(scope)) is not None:
            # the setup was refused twice just now: the peer is not asked a third time
            raise self._refusing(closed, task) from None
        token: list[object] = []
        try:
            async with self._use_session_inner(token) as session:
                yield session
                if self._sticky:
                    self._note_setup(scope, None)  # the call went through: the setup worked
        except _Closed as closed:
            raise self._refusing(closed.refusal, task) from None
        except Exception as failure:
            # a layer below refused a request: the protocol client saw a closed connection, the hook knows why. The
            # session's own token finds what its setup requests were refused, apart from other calls' sessions.
            reset = SESSION_TOKEN.set(token[0] if token else None)
            refusal: BaseException | None = None
            setup = not token  # no session was opened for the call
            try:
                # a call also makes requests the SDK adds (it lists the tools to check a result): refused, they are
                # the running call's refusal too
                asked = (task, "", _SDK_REQUEST) if task.startswith("tools/call") else (task, "")
                for hook in self.session_hooks:
                    for ask in asked:
                        refusal = hook.take_refusal(ask)
                        if refusal is not None:
                            setup = setup or ask == ""  # a request that sets a session up is kept under no operation
                            break
                    if refusal is not None:
                        break
            finally:
                SESSION_TOKEN.reset(reset)
            # a task group that holds the layer's own refusal stands for it, whichever hook did not keep it
            refusal = refusal or _refusal_in(failure)
            if refusal is not None:
                if self._sticky and setup:
                    self._note_setup(scope, refusal)
                raise refusal from None
            raise

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

    async def _retrying(self, attempt: Callable[[], Awaitable[Any]]) -> Any:
        """Run ``attempt``; when a held session turns out to be gone (the server forgot it: a restart, an expiry) retire
        this scope's one and run ``attempt`` once more on a new one. Only a client with session hooks (an identity
        layer below it) does: without one a call is never sent twice behind its caller's back. A refusal or any other
        failure is not retried."""
        try:
            return await attempt()
        except Exception as exc:
            if not (self._sticky and self.session_hooks and _session_gone(exc)):  # only with an identity scope
                raise
        gone = self._held.get(self._scope())
        if gone is not None:  # the session of this caller's scope only: another caller's is not the server's to forget
            await self._retire(gone)
        return await attempt()

    async def list_tools(self, *, user: str = "", agent_id: str = "") -> list[dict[str, Any]]:
        """Return the advertised set, following MCP ``nextCursor`` when enabled."""
        cached = self._cached_list(user)
        if cached is not None:
            self._emit_listed(cached, user, agent_id=agent_id)
            return copy.deepcopy(cached)
        return await self._retrying(lambda: self._list_tools_once(user, agent_id))

    async def _list_tools_once(self, user: str, agent_id: str = "") -> list[dict[str, Any]]:
        async with self._use_session("tools/list") as session:
            # Taken once the session is in hand: opening it may itself invalidate (a reconnect).
            generation = self._list_generation
            collected: list[dict[str, Any]] = []
            cursor: str | None = None
            seen: set[str] = set()
            server_ttl: int | None = None
            while True:
                meta = await self._meta_for(session, "tools/list")
                params = PaginatedRequestParams(cursor=cursor, _meta=meta or None) if cursor or meta else None
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
            # one crossing is one sent event, whether or not a gone session made the call run twice
            result = await self._retrying(lambda: self._call_tool_once(tool_name, arguments, **options))
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
        return result

    async def _call_tool_once(self, tool_name: str, arguments: dict[str, Any], **options: Any) -> dict[str, Any]:
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
        async with self._use_session(f"tools/call:{tool_name}") as session:
            meta = await self._meta_for(session, f"tools/call:{tool_name}", call_kwargs.get("meta"))
            if meta:
                call_kwargs["meta"] = meta
            result = await session.call_tool(tool_name, arguments, **call_kwargs)
            return result.model_dump(mode="json", by_alias=True)
