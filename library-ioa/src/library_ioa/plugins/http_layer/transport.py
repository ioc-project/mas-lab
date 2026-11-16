#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""layer/http: POST each send's payload to one url and return the response body."""

from __future__ import annotations

import asyncio
import contextlib
import re
import threading
from collections.abc import Mapping
from typing import Any, ClassVar
from urllib.parse import urlsplit, urlunsplit

import httpx
from mas.library.standard.lib.comm.capabilities import capability_set
from mas.library.standard.lib.comm.endpoint import LOOPBACK, split_url
from mas.library.standard.lib.comm.headers import validate_headers
from mas.library.standard.lib.comm.payload import MAX_MESSAGE_SIZE
from mas.library.standard.lib.stack import Envelope, Layer
from mas.library.standard.lib.stack.call import LayerLifecycle
from mas.library.standard.plugins.llm.http import resolve_ssl_verify
from mas.runtime.boundary.agentcomm.protocol import AgentCommError

# Headers the HTTP client writes from the request itself; a row or a send that set them could split or
# re-route the request.
_FRAMING_HEADERS = frozenset(
    {"host", "content-length", "transfer-encoding", "connection", "keep-alive", "upgrade", "te", "trailer"}
)


_UNSAFE_URL = re.compile(r"[\\\s\x00-\x1f\x7f]")


def _sendable(headers: Mapping[str, str] | None) -> dict[str, str]:
    """``headers`` validated, names lowercased; ``AgentCommError`` for a framing header."""
    checked = validate_headers(headers)
    framing = sorted(set(checked) & _FRAMING_HEADERS)
    if framing:
        raise AgentCommError(f"http transport sets {', '.join(framing)} itself")
    return checked


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
    if _UNSAFE_URL.search(text):
        raise AgentCommError("call transport url has a backslash, a space or a control character")
    parsed = urlsplit(text)
    if "@" in parsed.netloc:
        raise AgentCommError("call transport url must not carry credentials")
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise AgentCommError(f"call transport url {shown_url(text)!r} needs an http:// or https:// host")


class HttpCallTransport(LayerLifecycle):
    """The HTTP client under a protocol layer (``openai`` on llm calls, ``mcp`` on tool calls).

    It holds the row's headers, timeout, redirects and TLS settings; the protocol layer above posts
    through :attr:`client` (sync) or :attr:`aclient` (async, one per running event loop).
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
        self._headers = _sendable({str(k): str(v) for k, v in dict(headers or {}).items()}) if headers else {}
        # Without a timeout the MCP SDK's own budget applies: 30s to connect, 300s to read a stream.
        self.timeout = float(timeout) if timeout is not None else None
        scheme = urlsplit(self.url).scheme.lower()
        host = (urlsplit(self.url).hostname or "").lower()
        if bool(dev) and scheme == "https" and host in LOOPBACK:
            self._verify: Any = False
        else:
            self._verify = resolve_ssl_verify({"verify_ssl": verify_ssl, "ca_bundle": ca_bundle})
        self._client: httpx.Client | None = None
        # One async client per event loop, keyed by the loop's id. The loop is held so a closed one can be seen
        # and its client closed before it is forgotten.
        self._aclients: dict[int, tuple[asyncio.AbstractEventLoop, httpx.AsyncClient]] = {}
        self._lock = threading.Lock()

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

    def _options(self) -> dict[str, Any]:
        timeout = httpx.Timeout(self.timeout) if self.timeout is not None else httpx.Timeout(30.0, read=300.0)
        return {
            "timeout": timeout,
            "verify": self._verify,
            "headers": self._headers,
            "follow_redirects": self.follow_redirects,
        }

    @property
    def client(self) -> httpx.Client:
        with self._lock:
            if self._stopped:
                raise AgentCommError("http transport is stopped")
            if self._client is None:
                self._client = httpx.Client(**self._options())
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
                held = self._aclients[id(loop)] = (loop, httpx.AsyncClient(**self._options()))
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

    thread = threading.Thread(target=run, name="http-call-transport-close", daemon=True)
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
        # names are lowercased, like the envelope's, so one header is never sent twice under two spellings;
        # a name that is not a token or a value with a control character is refused here, not on the wire
        self._headers = {
            **validate_headers({"content-type": str(content_type)}),
            **_sendable({str(k): str(v) for k, v in dict(headers or {}).items()}),
        }
        self.timeout = float(timeout)
        # dev skips TLS verification, and only on a loopback https url
        self._verify = not (bool(dev) and scheme == "https" and host in LOOPBACK)
        self._client: httpx.Client | None = None
        self._lock = threading.Lock()

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
        if self._stopped:
            return
        self._stopped = True
        with self._lock:
            client, self._client = self._client, None
        if client is not None:
            client.close()

    def _http(self) -> httpx.Client:
        if self._stopped:
            raise AgentCommError("http transport is stopped")
        with self._lock:
            if self._client is None:
                self._client = httpx.Client(timeout=self.timeout, verify=self._verify)
            return self._client

    def _request_headers(self, envelope: Envelope) -> dict[str, Any]:
        out: dict[str, Any] = dict(self._headers)
        for key, value in _sendable(envelope.headers).items():
            out[key] = value.encode("utf-8")  # a header value may hold any text; send it as UTF-8 bytes
        return out

    def send(self, envelope: Envelope) -> Any:
        body = envelope.payload
        if isinstance(body, str):
            body = body.encode("utf-8")
        if not isinstance(body, (bytes, bytearray)):
            raise AgentCommError("http transport sends text or bytes")
        headers = self._request_headers(envelope)
        try:
            with self._http().stream("POST", self.url, content=bytes(body), headers=headers) as response:
                if response.status_code >= 400:
                    raise AgentCommError(f"http transport: {shown_url(self.url)} answered {response.status_code}")
                reply = bytearray()
                for chunk in response.iter_bytes():
                    reply.extend(chunk)
                    if len(reply) > self.max_message_size:
                        raise AgentCommError(
                            f"http transport: the reply of {shown_url(self.url)} is over {self.max_message_size} bytes"
                        )
                encoding = response.encoding or "utf-8"
        except httpx.HTTPError as exc:
            detail = str(exc).replace(self.url, shown_url(self.url))
            raise AgentCommError(f"http transport: {type(exc).__name__}: {detail}") from None
        return reply.decode(encoding, errors="replace")

    async def asend(self, envelope: Envelope) -> Any:
        return await asyncio.to_thread(self.send, envelope)
