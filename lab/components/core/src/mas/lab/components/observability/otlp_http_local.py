#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import time


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mas.lab.components.common.hooks import HookBus  # noqa: E402


class OtlpLocalHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        entry = {
            "timestamp": time(),
            "path": self.path,
            "content_type": self.headers.get("Content-Type"),
            "bytes": len(body),
        }
        self.server.storage_path.parent.mkdir(parents=True, exist_ok=True)
        with self.server.storage_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
        self.server.hook_bus.emit({
            "kind": "otlp_received",
            "payload": entry,
        })
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")


def main() -> None:
    host = os.getenv("OTLP_LOCAL_HOST", "0.0.0.0")
    port = int(os.getenv("OTLP_LOCAL_PORT", "4318"))
    storage_path = Path(os.getenv("OTLP_LOCAL_LOG", "logs/otel_otlp.jsonl"))
    server = ReusableThreadingHTTPServer((host, port), OtlpLocalHandler)
    server.storage_path = storage_path
    server.hook_bus = HookBus.from_env(default_hooks="ui", source="otlp_local")
    server.serve_forever()


if __name__ == "__main__":
    main()
