#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""LiveLlmEngine streaming: reassembles the same message shape the
non-streamed path returns, forwarding content deltas to ctx.on_stream_chunk
as they arrive."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from mas.runtime.engine.llm_live import LiveLlmEngine


class _FakeStreamResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self):
        return iter(self._lines)

    def __enter__(self) -> "_FakeStreamResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _FakeClient:
    def __init__(self, lines: list[str], **_: object) -> None:
        self._lines = lines

    def stream(self, method: str, url: str, *, json: object, headers: object, params: object = None):
        assert method == "POST"
        return _FakeStreamResponse(self._lines)

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _sse(chunk: dict) -> str:
    return f"data: {json.dumps(chunk)}"


def _install_fake_client(monkeypatch: pytest.MonkeyPatch, lines: list[str]) -> None:
    monkeypatch.setattr("httpx.Client", lambda **kwargs: _FakeClient(lines, **kwargs))


def test_streamed_content_is_reassembled_and_forwarded_chunk_by_chunk(monkeypatch):
    lines = [
        _sse({"choices": [{"delta": {"content": "Hel"}}]}),
        _sse({"choices": [{"delta": {"content": "lo, "}}]}),
        _sse({"choices": [{"delta": {"content": "world."}, "finish_reason": "stop"}]}),
        _sse({"usage": {"total_tokens": 7}}),
        "data: [DONE]",
    ]
    _install_fake_client(monkeypatch, lines)

    received: list[str] = []
    ctx = SimpleNamespace(on_stream_chunk=received.append)
    engine = LiveLlmEngine(ctx=ctx, stream=True, use_cache=False)

    message = engine._chat_completion([{"role": "user", "content": "hi"}], api_key="k", tools=None)

    assert received == ["Hel", "lo, ", "world."]
    assert message["content"] == "Hello, world."
    assert message["finish_reason"] == "stop"
    assert message["usage"] == {"total_tokens": 7}
    assert "tool_calls" not in message


def test_streamed_tool_calls_are_accumulated_across_chunks_and_not_forwarded(monkeypatch):
    lines = [
        _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "get_", "arguments": ""}}]
                        }
                    }
                ]
            }
        ),
        _sse(
            {
                "choices": [
                    {"delta": {"tool_calls": [{"index": 0, "function": {"name": "weather", "arguments": '{"city":'}}]}}
                ]
            }
        ),
        _sse(
            {
                "choices": [
                    {
                        "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"Paris"}'}}]},
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        ),
        "data: [DONE]",
    ]
    _install_fake_client(monkeypatch, lines)

    received: list[str] = []
    ctx = SimpleNamespace(on_stream_chunk=received.append)
    engine = LiveLlmEngine(ctx=ctx, stream=True, use_cache=False)

    message = engine._chat_completion(
        [{"role": "user", "content": "weather in paris"}], api_key="k", tools=[{"type": "function"}]
    )

    assert received == []  # tool-call arg deltas are never forwarded as chunks
    assert message["content"] is None
    assert message["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'},
        }
    ]
    assert message["finish_reason"] == "tool_calls"


def test_streaming_works_with_no_on_stream_chunk_installed(monkeypatch):
    lines = [_sse({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}), "data: [DONE]"]
    _install_fake_client(monkeypatch, lines)

    engine = LiveLlmEngine(ctx=SimpleNamespace(), stream=True, use_cache=False)
    message = engine._chat_completion([{"role": "user", "content": "hi"}], api_key="k", tools=None)

    assert message["content"] == "ok"


def test_streaming_with_no_ctx_at_all(monkeypatch):
    lines = [_sse({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}), "data: [DONE]"]
    _install_fake_client(monkeypatch, lines)

    engine = LiveLlmEngine(ctx=None, stream=True, use_cache=False)
    message = engine._chat_completion([{"role": "user", "content": "hi"}], api_key="k", tools=None)

    assert message["content"] == "ok"


def test_on_stream_chunk_exception_does_not_break_the_call(monkeypatch):
    lines = [
        _sse({"choices": [{"delta": {"content": "a"}}]}),
        _sse({"choices": [{"delta": {"content": "b"}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]
    _install_fake_client(monkeypatch, lines)

    def boom(_chunk: str) -> None:
        raise RuntimeError("boom")

    engine = LiveLlmEngine(ctx=SimpleNamespace(on_stream_chunk=boom), stream=True, use_cache=False)
    message = engine._chat_completion([{"role": "user", "content": "hi"}], api_key="k", tools=None)

    assert message["content"] == "ab"


def test_ignores_malformed_and_empty_sse_lines(monkeypatch):
    lines = [
        "",
        "event: ping",
        "data: not-json",
        _sse({"choices": [{"delta": {"content": "fine"}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]
    _install_fake_client(monkeypatch, lines)

    engine = LiveLlmEngine(ctx=SimpleNamespace(), stream=True, use_cache=False)
    message = engine._chat_completion([{"role": "user", "content": "hi"}], api_key="k", tools=None)

    assert message["content"] == "fine"


def test_non_streaming_path_is_unaffected_by_stream_field_default(monkeypatch):
    """stream defaults to False -- the existing non-streamed path (already
    covered elsewhere) must still be the one used unless explicitly opted in."""
    engine = LiveLlmEngine(use_cache=False)
    assert engine.stream is False
