#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Offline loopback: revoke after tool result 2, then clock_advance clears the cache."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

from mas.lab.benchmark.environment.engine import RunEnvironment
from mas.lab.benchmark.environment.model import RunCoordinates, compile_environment


class _Issuer:
    def __init__(self) -> None:
        self.allowed = {"cred": True}
        self.refresh_s = 20.0
        self.now = 0.0
        self.cache_until = 0.0
        self.calls_after_revoke = 0
        self.revoked_at: float | None = None

    def handle(self, path: str, body: bytes) -> tuple[int, dict]:
        if path == "/health":
            return 200, {"ok": True}
        if path == "/admin/revoke":
            self.allowed["cred"] = False
            self.revoked_at = self.now
            self.cache_until = self.now + self.refresh_s
            return 200, {"ok": True}
        if path.startswith("/status/"):
            if self.revoked_at is not None:
                if self.now >= self.cache_until:
                    return 200, {"allowed": 0}
                self.calls_after_revoke += 1
                return 200, {"allowed": 1}
            return 200, {"allowed": 1}
        if path == "/clock":
            payload = json.loads(body or b"{}")
            self.now = float(payload.get("now") or 0)
            return 200, {"now": self.now}
        return 404, {}


def _serve(issuer: _Issuer) -> tuple[HTTPServer, str]:
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            status, payload = issuer.handle(urlparse(self.path).path, b"")
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            status, payload = issuer.handle(urlparse(self.path).path, body)
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):
            return

    server = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


class _Fixture:
    def __init__(self, url: str) -> None:
        self._url = url

    def url(self, name: str) -> str:
        return self._url

    def port(self, name: str) -> int:
        return int(self._url.rsplit(":", 1)[-1])

    def host(self, name: str) -> str:
        return "127.0.0.1"

    def restart(self, name: str):
        return None

    def stop(self, name: str) -> None:
        return None

    def push_clock(self, now_s: float, *, scope_keys: tuple[str, ...] | None = None) -> None:
        from urllib.request import Request, urlopen

        body = json.dumps({"now": now_s}).encode()
        headers = {"Content-Type": "application/json"}
        urlopen(Request(f"{self._url}/clock", data=body, method="POST", headers=headers), timeout=2)


def _env(url: str) -> RunEnvironment:
    spec = compile_environment(
        {
            "clock": {"kind": "virtual"},
            "triggers": [
                {
                    "id": "revoke-after-2",
                    "when": {"on": "tool_result", "tool": "pay_fare", "occurrence": 2},
                    "do": [
                        {
                            "type": "http_call",
                            "config": {"method": "POST", "url": f"{url}/admin/revoke", "expect_status": 200},
                        }
                    ],
                },
                {
                    "id": "wait-refresh",
                    "when": {"on": "tool_result", "tool": "pay_fare", "occurrence": 3},
                    "do": [{"type": "clock_advance", "config": {"by": "21s"}}],
                },
            ],
        }
    )
    env = RunEnvironment(spec, run=RunCoordinates(item="revoked_mid", run="r1"))
    env.fixtures = _Fixture(url)  # type: ignore[assignment]
    return env


def test_revoked_mid_and_wait(tmp_path: Path) -> None:
    issuer = _Issuer()
    server, url = _serve(issuer)
    try:
        env = _env(url)
        env.vars["fixture.issuer.url"] = url

        class A:
            hook = "ingress"
            op = "TOOL_RESULT"
            tool = "pay_fare"
            agent = "payer"
            occurrence = 0

        from urllib.request import urlopen

        for n in (1, 2):
            A.occurrence = n
            env.on_transition("tool_result", tool="pay_fare", agent="payer", anchor=A())
        records = env.events.records()
        matched = [r for r in records if r["name"] == "trigger.matched" and r["source"]["id"] == "revoke-after-2"]
        assert matched and matched[0]["anchor"]["occurrence"] == 2
        idx = records.index(matched[0])
        following = records[idx + 1]
        assert following["name"] == "action.started"
        done = next(r for r in records[idx + 1 :] if r["name"] == "action.done")
        assert done["source"]["step"] == "http_call"
        before = json.loads(urlopen(f"{url}/status/cred").read())
        assert before["allowed"] == 1
        A.occurrence = 3
        env.on_transition("tool_result", tool="pay_fare", agent="payer", anchor=A())
        later = [r for r in env.events.records() if r.get("anchor", {}).get("occurrence") == 3]
        assert later
        assert records.index(done) < env.events.records().index(later[0])
        after = json.loads(urlopen(f"{url}/status/cred").read())
        assert after["allowed"] == 0
    finally:
        server.shutdown()


def test_determinism_strips_wall(tmp_path: Path) -> None:
    issuer = _Issuer()
    server, url = _serve(issuer)
    try:

        def run_once():
            env = _env(url)

            class A:
                hook = "ingress"
                op = "TOOL_RESULT"
                tool = "pay_fare"
                agent = "payer"
                occurrence = 2

            env.on_transition("tool_result", tool="pay_fare", agent="payer", anchor=A())
            out = []
            for rec in env.events.records():
                time = dict(rec["time"])
                time.pop("wall", None)
                time.pop("elapsed_ms", None)
                out.append({**rec, "time": time})
            return out

        a = run_once()
        b = run_once()
        assert [r["name"] for r in a] == [r["name"] for r in b]
    finally:
        server.shutdown()
