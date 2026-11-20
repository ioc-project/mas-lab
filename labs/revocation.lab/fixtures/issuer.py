#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Loopback credential issuer: /admin/revoke, /status/<id>, /clock."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--refresh-s", type=float, default=20.0)
    args = parser.parse_args()
    state = {"allowed": True, "now": 0.0, "revoked_at": None, "refresh_s": args.refresh_s}

    class Handler(BaseHTTPRequestHandler):
        def _read_json(self) -> dict | None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return None
            if length < 0 or length > 65_536:
                return None
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return None
            return body if isinstance(body, dict) else None

        def _send(self, code: int, payload: dict) -> None:
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path in {"/health", "/status"}:
                self._send(200, {"ok": True})
                return
            if path.startswith("/status/"):
                allowed = True
                if state["revoked_at"] is not None:
                    allowed = state["now"] < (state["revoked_at"] + state["refresh_s"])
                self._send(200, {"allowed": int(allowed)})
                return
            self._send(404, {})

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            body = self._read_json()
            if body is None:
                self._send(400, {"error": "bad request"})
                return
            if path == "/admin/revoke":
                state["allowed"] = False
                state["revoked_at"] = state["now"]
                self._send(200, {"ok": True})
                return
            if path == "/clock":
                try:
                    state["now"] = float(body.get("now") or 0)
                except (TypeError, ValueError):
                    self._send(400, {"error": "now must be a number"})
                    return
                self._send(200, {"now": state["now"]})
                return
            self._send(404, {})

        def log_message(self, *_args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    print(f"PORT={server.server_address[1]}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
