#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Loopback payments tool server: checks the issuer through a 20s status cache."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from urllib.request import urlopen


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--issuer", default="http://127.0.0.1:0")
    parser.add_argument("--refresh-s", type=float, default=20.0)
    args = parser.parse_args()
    cache = {"until": 0.0, "allowed": 1, "now": 0.0}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, payload: dict) -> None:
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            if urlparse(self.path).path == "/health":
                self._send(200, {"ok": True})
                return
            self._send(404, {})

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) if 0 < length <= 65_536 else b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                self._send(400, {"error": "bad request"})
                return
            if path == "/clock":
                try:
                    cache["now"] = float(body.get("now") or 0)
                except (TypeError, ValueError):
                    self._send(400, {"error": "now must be a number"})
                    return
                self._send(200, {"now": cache["now"]})
                return
            if path == "/pay":
                if cache["now"] >= cache["until"]:
                    try:
                        raw = urlopen(args.issuer.rstrip("/") + "/status/cred", timeout=2).read()
                        allowed = int(json.loads(raw).get("allowed") or 0)
                    except OSError:
                        allowed = 0
                    cache["allowed"] = allowed
                    cache["until"] = cache["now"] + args.refresh_s
                self._send(200, {"allowed": cache["allowed"]})
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
