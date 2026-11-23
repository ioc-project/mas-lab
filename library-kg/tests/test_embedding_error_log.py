#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A failed embedding batch logs the error's type and status.

Never the provider's body or a traceback.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any

import pytest
from secret_fakes import PROVIDER_KEY as KEY

from mas.library.kg.embeddings.state_embeddings import default_openai_embed_fn

pytest.importorskip("openai")

# The wire and SDK debug loggers are capped by the entry point (mas.ctl.logging_setup).
_CAPPED = ("httpcore", "httpx", "openai")


@pytest.fixture
def echo() -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: Any) -> None:
            return None

        def do_POST(self) -> None:  # noqa: N802
            self.rfile.read(int(self.headers.get("content-length") or 0))
            key = (self.headers.get("authorization") or "").split(" ", 1)[-1]
            body = json.dumps(
                {"error": {"message": f"echo {key}", "type": key, "code": key}}
            ).encode()
            self.send_response(400)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    ).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()


def test_a_failed_embedding_batch_logs_no_body(echo: str, caplog: pytest.LogCaptureFixture) -> None:
    # kills: ``logger.exception`` of an SDK error (its text and traceback carry the provider's body)
    embed = default_openai_embed_fn(model="m", api_base=echo, api_key=KEY)
    with caplog.at_level(logging.DEBUG):
        assert embed(["a state"]) == [None]
    ours = [r for r in caplog.records if not r.name.startswith(_CAPPED)]
    assert any("HTTP 400" in r.getMessage() for r in ours)
    assert all(KEY not in r.getMessage() and r.exc_info is None for r in ours)


@pytest.mark.parametrize(
    ("api_base", "expected"),
    [("", "https://api.openai.com/v1/"), ("https://embed.local/v1", "https://embed.local/v1/")],
)
def test_the_embedding_client_ignores_openai_base_url_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, api_base: str, expected: str
) -> None:
    import openai

    from mas.library.kg.embeddings import state_embeddings

    built: list[str] = []

    class Recording(openai.OpenAI):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            built.append(str(self.base_url))
            answer = SimpleNamespace(data=[SimpleNamespace(embedding=[1.0])])
            self.embeddings = SimpleNamespace(create=lambda **_: answer)

    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/never")
    monkeypatch.setattr(openai, "OpenAI", Recording)
    embed = state_embeddings.default_openai_embed_fn(model="m", api_base=api_base, api_key=KEY)
    assert embed(["a state"]) == [[1.0]]
    assert built == [expected]
