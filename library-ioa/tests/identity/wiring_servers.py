#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Loopback fakes for the identity wiring tests: stdlib servers that record every request they get.

* :class:`FakeMcp`: a streamable-HTTP MCP server (JSON answers) a test can script: extra fields in the
  ``initialize`` result, a gate that answers 401 with ``WWW-Authenticate``, a method that handles a
  ``challengeproof/present`` style request.
* :class:`FakeLlm`: an chat-completions-shaped chat-completions server.
* :func:`serve_a2a`: the real A2A SDK server (``A2AExposure``) behind a recording ASGI wrapper, with a
  scripted executor for the auth-required handshake.

Nothing here knows an identity scheme. A server decides from what it received, like a real peer.
"""

from __future__ import annotations

import json
import re
import socket
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

# --- what a server saw ----------------------------------------------------------------------------


@dataclass
class Seen:
    """One request as the peer received it."""

    method: str
    path: str
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body) if self.body else None

    def rpc_method(self) -> str:
        data = self.json()
        return str(data.get("method", "")) if isinstance(data, dict) else ""


@dataclass
class Reply:
    """What a scripted gate answers instead of the normal response."""

    status: int = 200
    headers: Mapping[str, str] = field(default_factory=dict)
    body: Any = None


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def wait_port(port: int) -> None:
    deadline = time.monotonic() + 5
    while True:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)


class _Server:
    """A threaded stdlib server; subclasses implement :meth:`handle`."""

    def __init__(self) -> None:
        self.seen: list[Seen] = []
        self._lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a: Any) -> None:
                return None

            def _dispatch(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length) if length else b""
                request = Seen(self.command, self.path, {k.lower(): v for k, v in self.headers.items()}, body)
                with owner._lock:
                    owner.seen.append(request)
                reply = owner.handle(request)
                payload = b""
                if reply.body is not None:
                    payload = reply.body if isinstance(reply.body, bytes) else json.dumps(reply.body).encode()
                self.send_response(reply.status)
                for name, value in reply.headers.items():
                    self.send_header(name, value)
                if reply.body is not None and "content-type" not in {k.lower() for k in reply.headers}:
                    self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(payload)

            do_POST = do_GET = do_DELETE = _dispatch

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        # a short poll interval: shutdown() waits for the loop to notice it (0.5 s by default) at the end of every test
        self._thread = threading.Thread(target=lambda: self._httpd.serve_forever(poll_interval=0.005), daemon=True)

    def handle(self, request: Seen) -> Reply:  # pragma: no cover - overridden
        raise NotImplementedError

    def __enter__(self) -> _Server:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(5)

    def posts(self, rpc_method: str = "") -> list[Seen]:
        with self._lock:
            return [s for s in self.seen if s.method == "POST" and (not rpc_method or s.rpc_method() == rpc_method)]


# --- MCP ------------------------------------------------------------------------------------------


class FakeMcp(_Server):
    """A streamable-HTTP MCP server answering with plain JSON.

    ``gate(request) -> Reply | None`` runs first on every POST (return a :class:`Reply` to answer it
    instead). ``initialize_extra`` is merged into the ``initialize`` result. ``on_method[method](rpc,
    request)`` answers a non-standard method with a result mapping (or raises ``Refuse``).
    ``tool_calls`` records ``(tool, arguments, meta)`` for every ``tools/call``.
    """

    def __init__(
        self,
        *,
        gate: Callable[[Seen], Reply | None] | None = None,
        initialize_extra: Mapping[str, Any] | Callable[[], Mapping[str, Any]] | None = None,
        on_method: Mapping[str, Callable[[dict[str, Any], Seen], Mapping[str, Any]]] | None = None,
        session_id: str = "",
    ) -> None:
        super().__init__()
        self.session_id = session_id
        self.gate = gate
        self.initialize_extra = initialize_extra or {}
        self.on_method = dict(on_method or {})
        self.tool_calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        self.initializes: list[dict[str, Any]] = []

    def handle(self, request: Seen) -> Reply:
        if request.method == "DELETE" and self.session_id:
            return Reply(200)
        if request.method != "POST":
            return Reply(405)
        if self.gate is not None:
            gated = self.gate(request)
            if gated is not None:
                return gated
        rpc = request.json()
        method = rpc.get("method", "")
        if "id" not in rpc:
            return Reply(202)
        if method == "initialize":
            self.initializes.append(rpc["params"])
            result: dict[str, Any] = {
                "protocolVersion": rpc["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-mcp", "version": "1"},
                **(self.initialize_extra() if callable(self.initialize_extra) else self.initialize_extra),
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {"name": "echo", "inputSchema": {"type": "object"}},
                    {"name": "other", "inputSchema": {"type": "object"}},
                ]
            }
        elif method == "tools/call":
            params = rpc["params"]
            self.tool_calls.append((params["name"], params.get("arguments") or {}, params.get("_meta") or {}))
            result = {"content": [{"type": "text", "text": f"{params['name']}:ok"}], "isError": False}
        elif method in self.on_method:
            try:
                result = dict(self.on_method[method](rpc, request))
            except Refuse as exc:
                return Reply(
                    200, body={"jsonrpc": "2.0", "id": rpc["id"], "error": {"code": -32001, "message": exc.reason}}
                )
        else:
            result = {}
        headers = {"mcp-session-id": self.session_id} if method == "initialize" and self.session_id else {}
        return Reply(200, headers=headers, body={"jsonrpc": "2.0", "id": rpc["id"], "result": result})


class Refuse(Exception):
    """A scripted server refusal; ``reason`` is a slug."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# --- LLM ------------------------------------------------------------------------------------------


class FakeLlm(_Server):
    """An chat-completions-shaped chat-completions server; ``gate`` works as in :class:`FakeMcp`."""

    def __init__(self, *, gate: Callable[[Seen], Reply | None] | None = None) -> None:
        super().__init__()
        self.gate = gate

    def handle(self, request: Seen) -> Reply:
        if self.gate is not None:
            gated = self.gate(request)
            if gated is not None:
                return gated
        body = {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}
        return Reply(200, body=body)


# --- A2A ------------------------------------------------------------------------------------------


class Recorder:
    """ASGI wrapper recording every request, optionally answering from ``gate`` before the app sees it."""

    def __init__(self, app: Any, seen: list[Seen], gate: Callable[[Seen], Reply | None] | None = None) -> None:
        self._app = app
        self._seen = seen
        self._gate = gate

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        body = b""
        more = True
        while more:
            event = await receive()
            body += event.get("body", b"")
            more = event.get("more_body", False)
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        request = Seen(scope["method"], scope["path"], headers, body)
        self._seen.append(request)
        if self._gate is not None:
            reply = self._gate(request)
            if reply is not None:
                raw = json.dumps(reply.body).encode() if reply.body is not None else b""
                out = [(k.lower().encode(), v.encode()) for k, v in reply.headers.items()]
                out += [(b"content-length", str(len(raw)).encode()), (b"content-type", b"application/json")]
                await send({"type": "http.response.start", "status": reply.status, "headers": out})
                await send({"type": "http.response.body", "body": raw})
                return
        sent = False

        async def replay() -> Any:
            nonlocal sent
            if sent:
                return await receive()
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self._app(scope, replay, send)


@dataclass
class A2aPeer:
    url: str
    seen: list[Seen]

    def sends(self) -> list[Seen]:
        return [s for s in self.seen if s.method == "POST" and s.rpc_method() == "SendMessage"]


@contextmanager
def serve_a2a(
    handler: Callable[..., Any],
    *,
    gate: Callable[[Seen], Reply | None] | None = None,
    executor: Any = None,
    inbound: Any = None,
    extensions: list[str] | None = None,
) -> Iterator[A2aPeer]:
    """The real A2A SDK server on a free loopback port, behind a recorder."""
    from library_ioa.plugins.a2a.exposure import A2AExposure
    from library_ioa.plugins.webserver import UvicornWebServer

    seen: list[Seen] = []
    port = free_port()
    url = f"http://127.0.0.1:{port}"

    class Exposure(A2AExposure):
        def build_app(self, manifest: dict[str, Any], handler: Any) -> Any:
            if executor is None:
                return Recorder(super().build_app(manifest, handler), seen, gate)
            from library_ioa.plugins.a2a.agentcard import agent_card_from_manifest
            from library_ioa.plugins.a2a.server.app import build_app

            declared = [{"uri": uri} for uri in extensions or []]
            card = agent_card_from_manifest(
                manifest, url=url, grpc_url=None, capabilities={"extensions": declared} if declared else {}
            )
            return Recorder(build_app(card, executor, inbound=self.inbound), seen, gate)

    exposure = Exposure(
        endpoint={"protocol": "a2a", "usage": "deploy", "url": url}, webserver=UvicornWebServer(), inbound=inbound
    )
    handle = exposure.serve_background({"metadata": {"name": "peer"}, "spec": {"description": "peer"}}, handler)
    try:
        wait_port(port)
        yield A2aPeer(url, seen)
    finally:
        handle.close()


# --- normalising what was seen, for the "no identity" goldens ----------------------------------------

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_DROP_HEADERS = frozenset(
    {"host", "user-agent", "content-length", "mcp-session-id", "date", "accept-encoding", "connection"}
)


def _without_sdk_noise(body: Any) -> Any:
    """Drop what an SDK version adds or omits without changing the call: an empty ``_meta``, an empty ``params``."""
    params = body.get("params") if isinstance(body, dict) else None
    if isinstance(params, dict):
        if not params.get("_meta"):
            params.pop("_meta", None)
        if not params:
            del body["params"]
    return body


def normalise(seen: list[Seen], port: int) -> list[dict[str, Any]]:
    """Requests as comparable data: no ports, no generated ids, header names and stable values only."""

    def clean(text: str) -> str:
        return _UUID.sub("<uuid>", text.replace(str(port), "<port>"))

    out: list[dict[str, Any]] = []
    for request in seen:
        body: Any = None
        if request.body:
            body = _without_sdk_noise(json.loads(clean(request.body.decode())))
        out.append(
            {
                "method": request.method,
                "path": request.path,
                "headers": {k: clean(v) for k, v in sorted(request.headers.items()) if k not in _DROP_HEADERS},
                "body": body,
            }
        )
    return out
