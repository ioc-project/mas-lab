#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Vertex generateContent protocol layer over a fake http transport."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from mas.library.standard.plugins.llm.gemini import GeminiLLMProvider
from mas.library.standard.plugins.llm.vertex import VertexLLMProvider

_OK = {
    "candidates": [{"content": {"role": "model", "parts": [{"text": "ok"}]}, "finishReason": "STOP"}],
    "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 2, "totalTokenCount": 6},
}


class _Transport:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        handler = httpx.MockTransport(self._answer)
        self.client = httpx.Client(transport=handler)
        self.aclient = httpx.AsyncClient(transport=handler)

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json=_OK)


def _chat(provider: VertexLLMProvider, **kwargs: Any) -> dict[str, Any]:
    return asyncio.run(provider.achat_completion(**kwargs))


def test_vertex_posts_project_path_with_bearer() -> None:
    transport = _Transport()
    provider = VertexLLMProvider.wrap(
        transport,
        {
            "api_base": "https://us-central1-aiplatform.googleapis.com",
            "api_key": {"secret": "vertex"},
            "project": "demo-proj",
            "location": "us-central1",
        },
    )
    out = _chat(provider, model="gemini-2.5-flash", messages=[{"role": "user", "content": "q"}])
    req = transport.requests[0]
    assert req.url.path.endswith(
        "/v1beta1/projects/demo-proj/locations/us-central1/publishers/google/models/gemini-2.5-flash:generateContent"
    )
    assert req.headers["Authorization"] == "Bearer secret"
    assert "x-goog-api-key" not in req.headers
    body = json.loads(req.content)
    assert body["contents"][0]["parts"][0]["text"] == "q"
    assert out["content"] == "ok"


def test_nested_proxy_project_reaches_the_post() -> None:
    transport = _Transport()
    provider = VertexLLMProvider.wrap(
        transport,
        {
            "proxy": {
                "api_base": "https://europe-west1-aiplatform.googleapis.com",
                "api_key": {"secret": "vertex"},
                "project": "demo-proj",
                "location": "europe-west1",
            }
        },
    )
    _chat(provider, model="gemini-2.5-flash", messages=[{"role": "user", "content": "q"}])
    path = transport.requests[0].url.path
    assert "/projects/demo-proj/locations/europe-west1/" in path
    assert path.endswith("models/gemini-2.5-flash:generateContent")


def test_missing_project_is_runtime_error() -> None:
    provider = VertexLLMProvider.wrap(
        _Transport(),
        {"api_base": "https://us-central1-aiplatform.googleapis.com", "api_key": {"secret": "vertex"}},
    )
    with pytest.raises(RuntimeError, match="proxy.project"):
        _chat(provider, model="gemini-2.5-flash", messages=[{"role": "user", "content": "q"}])


def test_protocol_layers_is_http_then_vertex() -> None:
    steps = VertexLLMProvider.protocol_layers({"api_base": "https://us-central1-aiplatform.googleapis.com"})
    assert [step["layer"] for step in steps] == ["http", "vertex"]
    assert issubclass(VertexLLMProvider, GeminiLLMProvider)
