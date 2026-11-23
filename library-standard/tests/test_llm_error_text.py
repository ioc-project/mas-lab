#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""An LLM failure says ``<Type>: HTTP <status>`` or ``<family> call failed``, never what the provider
sent: its message, type, code or request id may echo the request, the key included."""

from __future__ import annotations

import asyncio
import importlib
import json
import threading
import traceback
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from mas.runtime.reliability.classes import ClassifiedFailure
from secret_fakes import PROVIDER_KEY as KEY
from secret_fakes import bound_secrets

_FAMILIES = {
    "openai": ("openai", "OpenAILLMProvider", "/v1"),
    "anthropic": ("anthropic", "AnthropicLLMProvider", "/v1"),
    "gemini": ("gemini", "GeminiLLMProvider", "/v1beta"),
}


def _answer(mode: tuple[Any, ...], key: str) -> tuple[int, dict[str, str], bytes]:
    if mode[0] == "status":
        body = {"error": {"message": f"echo {key}", "type": key, "code": key, "status": key}}
        return mode[1], {"x-request-id": key}, json.dumps(body).encode()
    err = {"code": key, "type": key, "message": f"echo {key}"}
    return 200, {}, json.dumps({"error": err, "status": "failed"}).encode()


@pytest.fixture(scope="module")
def echo() -> Iterator[dict[str, Any]]:
    state: dict[str, Any] = {"mode": ("status", 400)}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: Any) -> None:
            return None

        def do_POST(self) -> None:  # noqa: N802
            self.rfile.read(int(self.headers.get("content-length") or 0))
            sent = (self.headers.get("authorization") or self.headers.get("x-api-key") or "").split(" ")[-1]
            status, headers, body = _answer(state["mode"], sent)
            self.send_response(status)
            for name, value in {
                "content-type": "application/json",
                "content-length": str(len(body)),
                **headers,
            }.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    state["base"] = f"http://127.0.0.1:{server.server_port}"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


def _fail(echo: dict[str, Any], family: str) -> ClassifiedFailure:
    module, name, path = _FAMILIES[family]
    cls = getattr(importlib.import_module(f"mas.library.standard.plugins.llm.{module}"), name)
    provider = cls.from_infra_spec({"api_base": f"{echo['base']}{path}", "api_key": {"secret": "k"}, "stream": False})
    with bound_secrets({"k": KEY}), pytest.raises(ClassifiedFailure) as raised:
        asyncio.run(provider.achat_completion(model="m", messages=[{"role": "user", "content": "q"}]))
    return raised.value


def _chain(exc: BaseException) -> str:
    texts, current = [], exc
    while current is not None and len(texts) < 20:
        texts.append(f"{current!s} {current!r}")
        current = current.__cause__ or current.__context__
    return "".join(texts) + "".join(traceback.format_exception(exc))


@pytest.mark.parametrize(
    ("family", "mode"),
    [
        ("openai", ("status", 401)),
        ("anthropic", ("status", 429)),
        ("gemini", ("status", 500)),
        ("openai", ("in_band",)),  # Anthropic sends an in-band error only as a stream event
        ("gemini", ("in_band",)),
    ],
)
def test_a_failure_shows_the_status_only(echo: dict[str, Any], family: str, mode: tuple[Any, ...]) -> None:
    # kills: the provider's message, type, code or x-request-id (an identifier-shaped key) copied into the failure
    echo["mode"] = mode
    failure = _fail(echo, family)
    assert KEY not in _chain(failure) and KEY not in str(failure.code)
    if mode[0] == "status":
        assert f"HTTP {mode[1]}" in str(failure)
