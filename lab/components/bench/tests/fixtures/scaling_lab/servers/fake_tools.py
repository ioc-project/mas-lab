#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Loopback MCP server that hosts the work tool, plus a GET /health port."""

from __future__ import annotations

import argparse
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _health_server(host: str, port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            payload = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18766)
    parser.add_argument("--health-port", type=int, default=18767)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)
    manifest = Path(__file__).resolve().parent.parent / "tools" / "work.tool.yaml"
    thread = threading.Thread(target=_health_server, args=(args.host, args.health_port), daemon=True)
    thread.start()
    from library_ioa.plugins.mcp.server import MCPToolServerFactory

    factory = MCPToolServerFactory.from_manifest(manifest, server_name="throughput-work")
    factory.run(
        transport="streamable-http",
        host=args.host,
        port=args.port,
        json_response=True,
        stateless_http=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
