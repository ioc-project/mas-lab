#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A loopback ClickHouse HTTP endpoint for tests: test support, shipped in no package."""

from __future__ import annotations

import base64
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

Answer = tuple[int, dict[str, str], bytes]


def echo_credentials(status: int = 516) -> Callable[[dict[str, str]], Answer]:
    """An answer that repeats the Basic credentials it received, in the body and the reason (as ClickHouse may)."""

    def respond(request: dict[str, str]) -> Answer:
        return status, {"X-ClickHouse-Exception-Code": "516"}, f"Code: 516. {request['credentials']}".encode()

    return respond


class FakeClickHouse:
    """Records each request (``path``, lower-case headers, decoded Basic ``credentials``) in :attr:`requests`
    and answers ``respond(request)`` as ``(status, headers, body)`` (default: 200, empty)."""

    def __init__(self, respond: Callable[[dict[str, str]], Answer] | None = None) -> None:
        self.requests: list[dict[str, str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                return None

            def _answer(self) -> None:
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                request = {"path": self.path, **{k.lower(): v for k, v in self.headers.items()}}
                auth = request.get("authorization", "")
                request["credentials"] = base64.b64decode(auth[6:]).decode() if auth.startswith("Basic ") else ""
                outer.requests.append(request)
                status, headers, body = respond(request) if respond else (200, {}, b"")
                self.send_response(status, f"bad {request['credentials']}" if status >= 400 else None)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _answer  # noqa: N815

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> FakeClickHouse:
        threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
