#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""layer/http: POST each send's payload to one url and return the response body."""

from __future__ import annotations

import asyncio
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
from mas.runtime.boundary.agentcomm.protocol import AgentCommError

# Headers the HTTP client writes from the request itself; a row or a send that set them could split or
# re-route the request.
_FRAMING_HEADERS = frozenset(
    {"host", "content-length", "transfer-encoding", "connection", "keep-alive", "upgrade", "te", "trailer"}
)


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
