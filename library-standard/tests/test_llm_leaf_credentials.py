#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Each LLMProxy leaf uses its own host and API key env."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mas.runtime.registry.llm_provider_registry import llm_providers_from_manifest


class _LeafHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self.server.auths.append(self.headers.get("Authorization"))  # type: ignore[attr-defined]
        body = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": self.server.label,  # type: ignore[attr-defined]
                        },
                        "finish_reason": "stop",
                    }
                ]
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return None


def _serve(label: str) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _LeafHandler)
    httpd.label = label  # type: ignore[attr-defined]
    httpd.auths = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def test_leaf_call_uses_that_leaf_api_key(monkeypatch) -> None:
    arm_http = _serve("from-arm")
    judge_http = _serve("from-judge")
    try:
        arm_base = f"http://127.0.0.1:{arm_http.server_address[1]}/v1"
        judge_base = f"http://127.0.0.1:{judge_http.server_address[1]}/v1"
        monkeypatch.setenv("ARM_KEY", "key-for-A")
        monkeypatch.setenv("JUDGE_KEY", "key-for-B")
        provider = llm_providers_from_manifest(
            None,
            llm_proxies={
                "arm": {
                    "api_base": arm_base,
                    "api_key_env": "ARM_KEY",
                    "allowed": ["m-a"],
                    "protocol": "openai",
                },
                "judge": {
                    "api_base": judge_base,
                    "api_key_env": "JUDGE_KEY",
                    "allowed": ["m-b"],
                    "protocol": "openai",
                },
            },
        )
        out = provider.chat_completion(
            model="m-b",
            messages=[{"role": "user", "content": "hi"}],
        )
        assert out["content"] == "from-judge"
        assert judge_http.auths == ["Bearer key-for-B"]  # type: ignore[attr-defined]
        assert arm_http.auths == []  # type: ignore[attr-defined]
    finally:
        arm_http.shutdown()
        judge_http.shutdown()
