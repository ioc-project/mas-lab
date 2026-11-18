#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A loopback OpenAI-compatible model server for tests: test support, shipped in no package."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class FakeOpenAI:
    """A local OpenAI-compatible endpoint. ``script(body)`` returns the assistant message for one request.

    ``mas-ctl chat`` runs in a subprocess, so the fake is a real loopback server: nothing leaves the
    machine, and the tests need neither a key nor a network.
    """

    def __init__(self, script: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.errors: list[BaseException] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
                outer.bodies.append(body)
                try:
                    message = script(body)
                except BaseException as exc:  # an assertion in the script must fail the test, not the client
                    outer.errors.append(exc)
                    self.send_error(500, "fake script failed")
                    return
                calls = message.get("tool_calls")
                reply = {
                    "id": f"fake-{len(outer.bodies)}",
                    "object": "chat.completion",
                    "model": body.get("model", "fake"),
                    "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if calls else "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                }
                payload = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/v1"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> FakeOpenAI:
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self.errors and exc_type is None:
            raise self.errors[0]

    def env(self) -> dict[str, str]:
        """The CLI environment that points ``standard:openai`` at this endpoint."""
        return {
            "OPENAI_BASE_URL": self.url,
            "OPENAI_API_KEY": "test-key",
            "MAS_INFRA_REFS": "standard:openai",
            # a host with a proxy configured must still reach the loopback fake directly
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }


def tool_call(name: str, arguments: dict[str, Any], call_id: str = "call-1") -> dict[str, Any]:
    """An assistant message that calls one tool."""
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
        ],
    }


def text(content: str) -> dict[str, Any]:
    """An assistant message that answers."""
    return {"role": "assistant", "content": content}


def last_role(body: dict[str, Any]) -> str:
    """The role of the last message in a request: ``tool`` once a tool result is back."""
    return str(body["messages"][-1]["role"])


def offered_tools(body: dict[str, Any]) -> set[str]:
    return {t["function"]["name"] for t in body.get("tools") or []}
