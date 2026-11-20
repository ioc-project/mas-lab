#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""OpenAI-compatible stub: three pay_fare tool calls, then a final answer."""

from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PLAN = [
    {"id": "c1", "type": "function", "function": {"name": "pay_fare", "arguments": "{}"}},
    {"id": "c2", "type": "function", "function": {"name": "pay_fare", "arguments": "{}"}},
    {"id": "c3", "type": "function", "function": {"name": "pay_fare", "arguments": "{}"}},
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--delay-s", type=float, default=0.0, help="hold each completion this long (a slow model)")
    args = parser.parse_args()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def do_POST(self) -> None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) if 0 < length <= 1_000_000 else b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                self.send_response(400)
                self.end_headers()
                return
            if args.delay_s > 0:
                time.sleep(args.delay_s)
            messages = body.get("messages") or []
            tool_results = sum(1 for m in messages if isinstance(m, dict) and m.get("role") == "tool")
            if tool_results >= len(PLAN):
                message = {"role": "assistant", "content": "done"}
                finish = "stop"
            else:
                message = {"role": "assistant", "content": None, "tool_calls": [PLAN[tool_results]]}
                finish = "tool_calls"
            if body.get("stream"):
                self._stream(message, finish)
                return
            payload = {
                "id": "chatcmpl-scripted",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _stream(self, message: dict, finish: str) -> None:
            """One content/tool-call chunk, one finish chunk, then [DONE] (OpenAI SSE)."""
            delta = {"role": "assistant"}
            if message.get("tool_calls"):
                delta["tool_calls"] = [{"index": 0, **call} for call in message["tool_calls"]]
            else:
                delta["content"] = message.get("content") or ""
            chunks = [
                {
                    "id": "chatcmpl-scripted",
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": delta}],
                },
                {
                    "id": "chatcmpl-scripted",
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                },
            ]
            raw = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
            data = raw.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    print(f"PORT={server.server_address[1]}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
