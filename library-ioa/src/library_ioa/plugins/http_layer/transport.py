#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""layer/http: POST each send's payload to one url and return the response body."""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Callable, Mapping
from typing import Any, ClassVar
from urllib.parse import urlsplit, urlunsplit

import httpx
from mas.library.standard.lib.comm.capabilities import capability_set
from mas.library.standard.lib.comm.endpoint import split_url
from mas.library.standard.lib.comm.headers import validate_headers
from mas.library.standard.lib.comm.payload import MAX_MESSAGE_SIZE
from mas.library.standard.lib.comm.text import safe_text
from mas.library.standard.lib.stack import AuthorizableTransport, Envelope, Layer, SessionHook
from mas.library.standard.lib.tls import resolve_ssl_verify
from mas.runtime.aio import LoopCache, new_thread
from mas.runtime.boundary.agentcomm.protocol import AgentCommError
from mas.runtime.secrets import is_loopback_host
from mas.runtime.secrets.transport import reveal_headers_for
from mas.runtime.secrets.url import has_unsafe_chars, origin

# Headers the HTTP client writes from the request itself; a row or a send that set them could split or
# re-route the request.
_FRAMING_HEADERS = frozenset(
    {"host", "content-length", "transfer-encoding", "connection", "keep-alive", "upgrade", "te", "trailer"}
)


def sendable(headers: Mapping[str, str] | None) -> dict[str, str]:
    """``headers`` validated, names lowercased; ``AgentCommError`` for a framing header."""
    checked = validate_headers(headers)
    framing = sorted(set(checked) & _FRAMING_HEADERS)
    if framing:
        raise AgentCommError(f"http transport sets {', '.join(framing)} itself")
    return checked


def _check_header_names(headers: Mapping[str, Any] | None) -> None:
    """Refuse a framing or unsendable header name now. SecretRef values are not stringified or stored."""
    from mas.runtime.secrets.ref import is_ref

    probe: dict[str, str] = {}
    for key, value in dict(headers or {}).items():
        if is_ref(value):
            probe[str(key)] = "x"
        else:
            probe[str(key)] = str(value)
    if probe:
        sendable(probe)


def _awaitable(call: Any) -> Any:
    """``call`` as the coroutine function an async client's event hook must be."""

    async def hook(request: httpx.Request) -> None:
        call(request)

    return hook


class _StaticFirst(httpx.Auth):
    """Put a row's static headers on the request, then let ``auth`` present and sign it."""

    def __init__(self, hook: Any, auth: httpx.Auth) -> None:
        self._hook = hook
        self._auth = auth
        self.requires_request_body = auth.requires_request_body
        self.requires_response_body = auth.requires_response_body

    def sync_auth_flow(self, request: httpx.Request):  # type: ignore[no-untyped-def]
        self._hook.apply(request)
        flow = self._auth.sync_auth_flow(request)
        try:
            sent = next(flow)
            while True:
                sent = flow.send((yield sent))
        except StopIteration:
            return

    async def async_auth_flow(self, request: httpx.Request):  # type: ignore[no-untyped-def]
        self._hook.apply(request)
        flow = self._auth.async_auth_flow(request)
        try:
            sent = await flow.__anext__()
            while True:
                sent = await flow.asend((yield sent))
        except StopAsyncIteration:
            return


def shown_url(url: str) -> str:
    """``url`` as an error or a log may show it: without its query or fragment, which can carry a secret."""
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def check_url(url: str) -> tuple[str, str]:
    """``(scheme, host)`` of an http transport url; ``AgentCommError`` when it is not one this layer accepts.

    ``https`` anywhere; plain ``http`` only on ``127.0.0.1``, ``localhost`` or
    ``::1``. No credentials in the url, no backslash, space or control character.
    """
    return split_url(url, what="http transport")


def _http_params(params: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(params or {})
    timeout = raw.get("timeout")
    return {
        "url": str(raw.get("url") or "").strip(),
        "headers": raw.get("headers"),
        "timeout": float(timeout) if timeout is not None else 30.0,
        "dev": bool(raw.get("dev")),
        "content_type": str(raw.get("content_type") or "application/json"),
        "max_message_size": (
            int(raw["max_message_size"]) if raw.get("max_message_size") is not None else MAX_MESSAGE_SIZE
        ),
    }


def _call_url(url: str) -> None:
    """A call endpoint (llm, tool server) is http or https; no credentials, no control characters.

    Unlike the send transport it may be plain http on any host: an in-cluster gateway is not loopback.
    """
    text = str(url or "").strip()
    if not text:
        return
    if has_unsafe_chars(text):
        raise AgentCommError("call transport url has a backslash, a space or a control character")
    parsed = urlsplit(text)
    if "@" in parsed.netloc:
        raise AgentCommError("call transport url must not carry credentials")
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise AgentCommError(f"call transport url {shown_url(text)!r} needs an http:// or https:// host")


class HttpCallTransport(AuthorizableTransport):
    """The HTTP client under a protocol layer (``openai`` on llm calls, ``mcp`` on tool calls).

    It holds the row's headers, timeout, redirects and TLS settings; the protocol layer above posts
    through :attr:`client` (sync) or :attr:`aclient` (async, one per running event loop).

    A layer above the transport may install an ``httpx.Auth`` (:meth:`use_auth`): every request of both clients
    passes through it, so it can add headers per request and answer a 401 once. It may also offer a session hook
    (:meth:`add_session_hook`) to the protocol layer above. With neither, the requests are what they were.
    """

    needs_lower: ClassVar[bool] = False
    per = "destination"
    call_kinds = ("llm", "tool")
    priority = 0

    def __init__(
        self,
        *,
        url: str = "",
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        dev: bool = False,
        verify_ssl: bool | None = None,
        ca_bundle: str | None = None,
        follow_redirects: bool = False,
    ) -> None:
        super().__init__()
        self.url = str(url or "").strip()
        self.follow_redirects = bool(follow_redirects)
        _call_url(self.url)
        from mas.runtime.secrets.ref import check_http_header_secrets

        self._header_refs = dict(headers or {})
        check_http_header_secrets(self._header_refs)
        _check_header_names(self._header_refs)
        # Without a timeout the MCP SDK's own budget applies: 30s to connect, 300s to read a stream.
        self.timeout = float(timeout) if timeout is not None else None
        scheme, host, _port = origin(self.url)
        if bool(dev) and scheme == "https" and is_loopback_host(host):
            self._verify: Any = False
        else:
            self._verify = resolve_ssl_verify({"verify_ssl": verify_ssl, "ca_bundle": ca_bundle})
        from library_ioa.utils.secret_headers import SecretHeaderHook

        # Headers are set on each request (a rotated secret is picked up, none is kept in the client), and a
        # secret header only goes to this url's origin; a redirect may not leave it.
        self._headers_hook = SecretHeaderHook(
            self._header_refs,
            owner_url=self.url,
            error=AgentCommError,
            what="http call transport",
            prepare=sendable,
        )
        self._client: httpx.Client | None = None
        # One async client per event loop, keyed by the loop's id. The loop is held so a closed one can be seen
        # and its client closed before it is forgotten.
        self._aclients: dict[int, tuple[asyncio.AbstractEventLoop, httpx.AsyncClient]] = {}
        self._lock = threading.Lock()
        self._auth: httpx.Auth | None = None
        self._auth_guard: Callable[[httpx.Request], None] | None = None
        self._hooks: list[SessionHook] = []

    def use_auth(self, auth: httpx.Auth, *, guard: Callable[[httpx.Request], None] | None = None) -> None:
        with self._lock:
            if self._client is not None or self._aclients:
                raise AgentCommError("http transport: install auth before the first request")
            if self._auth is not None:  # two layers presenting on one request would each overwrite the other
                raise AgentCommError("http transport: an auth layer is installed already")
            self._auth = auth
            self._auth_guard = guard

    def static_header_names(self) -> list[str]:
        """The header names of the row: what an auth layer's evidence must not collide with."""
        return [str(name) for name in self._header_refs]

    def add_session_hook(self, hook: SessionHook) -> None:
        with self._lock:
            self._hooks.append(hook)

    def session_hooks(self) -> list[SessionHook]:
        with self._lock:
            return list(self._hooks)

    @classmethod
    def from_params(cls, params: Mapping[str, Any] | None) -> "HttpCallTransport":
        raw = dict(params or {})
        timeout = raw.get("timeout")
        return cls(
            url=str(raw.get("url") or ""),
            headers=raw.get("headers"),
            timeout=float(timeout) if timeout is not None else None,
            dev=bool(raw.get("dev")),
            verify_ssl=raw.get("verify_ssl"),
            ca_bundle=raw.get("ca_bundle"),
            follow_redirects=bool(raw.get("follow_redirects")),
        )

    def _same_origin(self, request: httpx.Request) -> None:
        """Refuse a request to another origin than the row's url (a redirect hop) while an auth layer is installed.

        An auth layer put its credentials on the first request; a redirect would carry them, and a body with them,
        to whoever the answer names.
        """
        sent = request.url
        here = httpx.URL(self.url)
        ports = {"http": 80, "https": 443}

        def origin(url: httpx.URL) -> tuple[str, str, int]:
            return url.scheme.lower(), url.host.lower(), url.port or ports.get(url.scheme.lower(), 0)

        # a url with credentials in it is refused as a place, as the identity auth's own guard refuses it
        if sent.userinfo or origin(sent) != origin(here):
            raise AgentCommError(
                "http transport: a redirect to another origin is refused while an auth layer is installed"
            )

    async def _same_origin_async(self, request: httpx.Request) -> None:
        self._same_origin(request)

    def _options(self, *, sync: bool) -> dict[str, Any]:
        timeout = httpx.Timeout(self.timeout) if self.timeout is not None else httpx.Timeout(30.0, read=300.0)
        hook = self._headers_hook
        hooks: dict[str, list[Any]] = {"request": [hook.apply if sync else hook.arequest], "response": []}
        if self.follow_redirects:
            hooks["response"].append(hook.check_redirect if sync else hook.acheck_redirect)
        from mas.runtime.secrets.transport import httpx_client_kwargs_for

        options: dict[str, Any] = {
            "timeout": timeout,
            "verify": self._verify,
            "follow_redirects": self.follow_redirects,
            "event_hooks": hooks,
            **httpx_client_kwargs_for(self.url),
        }
        if self._auth is not None:
            # the row's headers go on before the auth layer signs, so a signature covers them
            options["auth"] = _StaticFirst(hook, self._auth)
            hooks["request"].append(self._same_origin if sync else self._same_origin_async)
            guard = self._auth_guard  # the auth's own place check of each hop
            if guard is not None:
                hooks["request"].append(guard if sync else _awaitable(guard))
        return options

    @property
    def client(self) -> httpx.Client:
        with self._lock:
            if self._stopped:
                raise AgentCommError("http transport is stopped")
            if self._client is None:
                self._client = httpx.Client(**self._options(sync=True))
            return self._client

    @property
    def aclient(self) -> httpx.AsyncClient:
        """The async client of the running loop. Another loop has its own; none is closed from under a request."""
        if self._stopped:
            raise AgentCommError("http transport is stopped")
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._stopped:
                raise AgentCommError("http transport is stopped")
            orphans = [key for key, (other, _client) in self._aclients.items() if other.is_closed()]
            dropped = [self._aclients.pop(key)[1] for key in orphans]
            held = self._aclients.get(id(loop))
            if held is None or held[1].is_closed:
                held = self._aclients[id(loop)] = (loop, httpx.AsyncClient(**self._options(sync=False)))
            client = held[1]
        for orphan in dropped:
            _close_orphan(orphan)
        return client

    def discard_client(self) -> None:
        """Drop the sync client after a transport failure; the next call opens a fresh connection."""
        with self._lock:
            client, self._client = self._client, None
        if client is not None:
            with contextlib.suppress(Exception):
                client.close()

    async def adiscard_aclient(self) -> None:
        """Drop this loop's async client after a transport failure."""
        loop = asyncio.get_running_loop()
        with self._lock:
            held = self._aclients.pop(id(loop), None)
        if held is not None:
            with contextlib.suppress(Exception):
                await held[1].aclose()

    def stop(self) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            client, self._client = self._client, None
            aclients = list(self._aclients.values())
            self._aclients.clear()
        if client is not None:
            client.close()
        try:
            current: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        for loop, aclient in aclients:
            if aclient.is_closed:
                continue
            if loop is current:
                loop.create_task(aclient.aclose())
            elif loop.is_running():
                asyncio.run_coroutine_threadsafe(aclient.aclose(), loop)
            else:
                _close_orphan(aclient)


def _close_orphan(client: httpx.AsyncClient) -> None:
    """Close a client whose loop has stopped, on a short-lived loop of its own so its sockets are released."""
    if client.is_closed:
        return

    def run() -> None:
        with contextlib.suppress(Exception):
            asyncio.run(client.aclose())

    thread = new_thread(target=run, name="http-call-transport-close", daemon=True)
    thread.start()
    thread.join(5)


class HttpLayer(Layer):
    """The bottom layer of a stack: one HTTP POST per send.

    The envelope ``payload`` (text or bytes) is the request body, sent with
    ``content_type`` (default ``application/json``). The request headers are
    ``headers`` from the params, then the envelope's headers (the stack's
    ``traceparent``, a protocol layer's own headers); an envelope header wins
    on a clash. ``host``, ``content-length``, ``transfer-encoding`` and the other
    framing headers are the client's own: a row or an envelope that sets one is
    refused. The response body is the reply; a body over ``max_message_size``
    bytes (default 1 MiB, as for ``slim``) is refused. Errors show the url
    without its query or fragment. A connection error, a timeout
    or a status of 400 or more raises ``AgentCommError``. The url is the
    address, so the envelope's ``target`` is not used.

    This transport only connects. It never receives, so a stack whose bottom
    layer is ``http`` cannot serve an agent (``usage: listen``); serving over
    HTTP is the ``a2a`` ``agent_expose`` plugin's job.
    """

    needs_lower: ClassVar[bool] = False
    CAPABILITIES: ClassVar[dict[str, bool]] = capability_set(unicast=True)
    LISTEN_REFUSAL: ClassVar[str] = (
        "the http transport only connects; it cannot serve an agent from this process. "
        "Serve an agent over HTTP with the a2a agent_expose plugin (mas-ctl serve)"
    )

    def __init__(
        self,
        *,
        url: str,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
        dev: bool = False,
        content_type: str = "application/json",
        max_message_size: int = MAX_MESSAGE_SIZE,
    ) -> None:
        super().__init__()
        scheme, host = check_url(url)
        if isinstance(max_message_size, bool) or not isinstance(max_message_size, int) or max_message_size <= 0:
            raise AgentCommError("http transport max_message_size must be a positive number of bytes")
        self.max_message_size = max_message_size
        self.url = str(url).strip()
        self.endpoint = self.url
        from mas.runtime.secrets.ref import check_http_header_secrets

        # Keep SecretRef objects until send. Stringifying here would put ``secret:name/key`` on the wire.
        self._header_refs = dict(headers or {})
        check_http_header_secrets(self._header_refs)
        _check_header_names(self._header_refs)
        self._headers = validate_headers({"content-type": str(content_type)})
        self.timeout = float(timeout)
        # dev skips TLS verification, and only on a loopback https url
        self._verify = not (bool(dev) and scheme == "https" and is_loopback_host(host))
        self._clients = LoopCache()  # one client per event loop: connections are reused across sends

    @classmethod
    def wrap(
        cls,
        inner: Any,
        params: dict[str, Any] | None = None,
        *,
        call_kinds: tuple[str, ...] | None = None,
    ) -> Any:
        """Send transport, or an llm/tool POST client when ``for`` includes those kinds."""
        kinds = {str(kind) for kind in (call_kinds or ())}
        if kinds & {"llm", "tool"}:
            if isinstance(inner, HttpCallTransport):
                return inner
            return HttpCallTransport.from_params(params)
        del inner
        raw = _http_params(params)
        return cls(
            url=raw["url"],
            headers=raw["headers"],
            timeout=raw["timeout"],
            dev=raw["dev"],
            content_type=raw["content_type"],
            max_message_size=raw["max_message_size"],
        )

    def __repr__(self) -> str:
        return f"HttpLayer(url={shown_url(self.url)!r})"

    def stop(self) -> None:
        self._stopped = True
        self._clients.close()

    def _request_headers(self, envelope: Envelope) -> dict[str, Any]:
        out: dict[str, Any] = dict(self._headers)
        if self._header_refs:
            out.update(sendable(reveal_headers_for(self.url, self._header_refs, what="http transport headers")))
        for key, value in sendable(envelope.headers).items():
            out[key] = value.encode("utf-8")  # a header value may hold any text; send it as UTF-8 bytes
        return out

    async def asend(self, envelope: Envelope) -> Any:
        if self._stopped:
            raise AgentCommError("http transport is stopped")
        body = envelope.payload
        if isinstance(body, str):
            body = body.encode("utf-8")
        if not isinstance(body, (bytes, bytearray)):
            raise AgentCommError("http transport sends text or bytes")
        headers = self._request_headers(envelope)
        try:
            client = self._clients.get(lambda: httpx.AsyncClient(timeout=self.timeout, verify=self._verify))
            async with client.stream("POST", self.url, content=bytes(body), headers=headers) as response:
                if response.status_code >= 400:
                    raise AgentCommError(f"http transport: {shown_url(self.url)} answered {response.status_code}")
                reply = bytearray()
                async for chunk in response.aiter_bytes():
                    reply.extend(chunk)
                    if len(reply) > self.max_message_size:
                        limit = self.max_message_size
                        raise AgentCommError(
                            f"http transport: the reply of {shown_url(self.url)} is over {limit} bytes"
                        )
                encoding = response.encoding or "utf-8"
        except httpx.HTTPError as exc:
            detail = safe_text(str(exc).replace(self.url, shown_url(self.url)))
            raise AgentCommError(f"http transport: {type(exc).__name__}: {detail}") from None
        return reply.decode(encoding, errors="replace")
