#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""OpenAI-compatible loopback model: call work, then echo the tool result."""

from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def _user_text(messages: list[dict[str, Any]]) -> str:
    for message in messages:
        if message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "\n".join(
                    str(part.get("text") or "")
                    for part in content
                    if isinstance(part, dict) and str(part.get("type") or "text") == "text"
                )
            return ""
    return ""


def _last_role(messages: list[dict[str, Any]]) -> str:
    if not messages:
        return ""
    return str(messages[-1].get("role") or "")


def _work_rounds(prompt: str) -> int:
    parts = prompt.strip().split()
    lowered = [part.lower() for part in parts]
    if "work" in lowered:
        index = lowered.index("work")
        if index + 1 < len(parts) and parts[index + 1].isdigit():
            return max(1, int(parts[index + 1]))
    if parts and parts[-1].isdigit():
        return max(1, int(parts[-1]))
    return 1


def _wait_s(prompt: str) -> float | None:
    parts = prompt.lower().split()
    if "wait" not in parts:
        return None
    index = parts.index("wait")
    if index + 1 < len(parts):
        try:
            return max(0.0, float(parts[index + 1]))
        except ValueError:
            return 0.05
    return 0.05


def script(body: dict[str, Any]) -> dict[str, Any]:
    messages = list(body.get("messages") or [])
    prompt = _user_text(messages)
    if _last_role(messages) == "user":
        delay = _wait_s(prompt)
        if delay:
            time.sleep(delay)
        if "work" not in prompt.lower() and delay is not None:
            return {"role": "assistant", "content": "waited"}
    if _last_role(messages) != "tool":
        rounds = _work_rounds(prompt)
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-work",
                    "type": "function",
                    "function": {
                        "name": "work",
                        "arguments": json.dumps({"n": rounds}),
                    },
                }
            ],
        }
    last = messages[-1]
    return {"role": "assistant", "content": f"done:{last.get('content') or ''}"}


def _completion(body: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
    calls = message.get("tool_calls")
    return {
        "id": "fake-throughput",
        "object": "chat.completion",
        "model": body.get("model", "gpt-4o-mini"),
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if calls else "stop",
            }
        ],
        "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
    }


def _sse_chunks(body: dict[str, Any], message: dict[str, Any]) -> list[bytes]:
    model = body.get("model", "gpt-4o-mini")
    calls = message.get("tool_calls") or []
    chunks: list[dict[str, Any]] = []
    if calls:
        first = {
            "index": 0,
            "delta": {"role": "assistant", "tool_calls": [dict(calls[0], index=0)]},
            "finish_reason": None,
        }
        chunks.append({"id": "fake-throughput", "object": "chat.completion.chunk", "model": model, "choices": [first]})
        done = {"index": 0, "delta": {}, "finish_reason": "tool_calls"}
        chunks.append({"id": "fake-throughput", "object": "chat.completion.chunk", "model": model, "choices": [done]})
    else:
        first = {
            "index": 0,
            "delta": {"role": "assistant", "content": message.get("content") or ""},
            "finish_reason": None,
        }
        chunks.append({"id": "fake-throughput", "object": "chat.completion.chunk", "model": model, "choices": [first]})
        done = {"index": 0, "delta": {}, "finish_reason": "stop"}
        chunks.append({"id": "fake-throughput", "object": "chat.completion.chunk", "model": model, "choices": [done]})
    lines = [f"data: {json.dumps(chunk)}\n\n".encode() for chunk in chunks]
    lines.append(b"data: [DONE]\n\n")
    return lines


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/health", "/health/", "/"}:
            payload = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path.rstrip("/").endswith("/models"):
            payload = json.dumps({"data": [{"id": "gpt-4o-mini", "object": "model"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
        body = json.loads(raw or b"{}")
        message = script(body)
        if body.get("stream"):
            chunks = _sse_chunks(body, message)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in chunks:
                self.wfile.write(chunk)
                self.wfile.flush()
            return
        payload = json.dumps(_completion(body, message)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args: object) -> None:
        return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
