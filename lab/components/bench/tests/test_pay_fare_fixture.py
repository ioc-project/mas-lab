#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from urllib.parse import urlparse

from mas.library.standard.lib.environment_port import reset_environment_port, set_environment_port

ROOT = Path(__file__).resolve().parents[4]


def _load_pay_fare():
    path = ROOT / "labs" / "revocation.lab" / "tools" / "pay_fare.py"
    spec = spec_from_file_location("revocation_pay_fare", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.PayFareTool


def test_pay_fare_posts_pay_at_bound_fixture() -> None:
    paid: list[str] = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            paid.append(urlparse(self.path).path)
            body = json.dumps({"allowed": 1}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            return

    server = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}"

    class Port:
        def fixture_url(self, name):
            assert name == "payments"
            return url

        def observe(self, on, *, tool="", agent="", hook="", op=""):
            return None

        def fire(self, trigger_id, anchor):
            return None

        def triggers(self):
            return ()

        def note_error(self, message):
            return None

        def bind_control(self, control, *, session_id="", entry_agent=""):
            return None

        def run_named_action(self, name):
            return None

        def emit_named(self, name, payload=None, *, source=None):
            return {}

        def deliver_event(self, name, payload=None, *, source=None):
            return False

    token = set_environment_port(Port())
    try:
        tool = _load_pay_fare()()
        result = tool.execute()
        assert result == {"allowed": 1}
        assert paid == ["/pay"]
    finally:
        reset_environment_port(token)
        server.shutdown()


def test_pay_fare_garbage_json_is_not_allowed() -> None:
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = b"not-json"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            return

    server = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    try:
        tool = _load_pay_fare()(url=f"http://{host}:{port}")
        result = tool.execute()
        assert result["allowed"] == 0
        assert result.get("error")
    finally:
        server.shutdown()
