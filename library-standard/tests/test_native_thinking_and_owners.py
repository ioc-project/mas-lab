#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Native thinking records, record owners, memory stores and RAG.

* ``thinking_start`` / ``thinking_end`` are emitted from ``llm.thinking``
  boundary records as the runtime reports them, with the state contract of
  the LLM call they belong to.
* Every kernel record that becomes a node names its owner
  (``parent_call_id``): the call that was running when it happened.
* A memory call names its store; there is no RAG query made from a context part.
* A streamed completion through the OpenAI plugin and the live engine reports
  the real reasoning boundaries.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from mas.library.standard.lib.observability.native.boundary_handlers import THINKING_CALL_SUFFIX
from mas.library.standard.lib.observability.native.transform import NativeObservabilityTransform, TransformContext

MESSAGES = [{"role": "user", "content": "Go."}]


def _feed(records: list[dict], ctx: TransformContext) -> list[dict]:
    transform = NativeObservabilityTransform()
    out: list[dict] = []
    for record in records:
        out.extend(transform.transform(record, ctx=ctx))
    return out


def _user(call_id: str = "exec-root") -> dict:
    return {"_source": "session", "session_kind": "user_input", "text": "Plan", "call_id": call_id, "turn_id": "t1"}


def _boundary(kind: str, payload: dict, cid: int = 1, **extra) -> dict:
    return {"_source": "boundary", "kind": kind, "correlation_id": cid, "payload": payload, **extra}


def _llm_start(cid: int = 1) -> dict:
    return _boundary("engine.io", {"op": "LLM_CALL", "messages": MESSAGES, "model": "m"}, cid)


def _llm_end(cid: int = 1) -> dict:
    return _boundary("engine.io.return", {"op": "LLM_CALL", "text": "answer", "next_step": "STOP"}, cid)


def _thinking(boundary: str, at: float, text: str = "", cid: int = 1) -> dict:
    return _boundary("llm.thinking", {"op": "LLM_CALL", "boundary": boundary, "at": at, "text": text}, cid)


def _ctx() -> TransformContext:
    return TransformContext(agent_id="root", run_id="r1", shared={})


def test_thinking_records_follow_the_llm_call_state_contract() -> None:
    out = _feed(
        [_user(), _llm_start(), _thinking("start", 10.0), _thinking("end", 12.5, "step one"), _llm_end()],
        _ctx(),
    )
    llm = next(r for r in out if r["kind"] == "llm_call_start")
    start = next(r for r in out if r["kind"] == "thinking_start")
    end = next(r for r in out if r["kind"] == "thinking_end")
    think_id = llm["call_id"] + THINKING_CALL_SUFFIX
    for rec in (start, end):
        assert rec["call_id"] == think_id
        assert rec["parent_call_id"] == llm["call_id"]
        assert (rec["state_in"], rec["state_in_content"]) == (llm["state_in"], llm["state_in_content"])
        assert rec["state_out"] == f"{think_id}#0"
    # Stamped with the time the runtime reported, not the time the record was processed.
    assert (start["timestamp"], end["timestamp"]) == (10.0, 12.5)
    assert "state_out_content" not in start
    assert end["state_out_content"] == "step one"


def test_thinking_records_do_not_depend_on_their_order() -> None:
    """Each thinking record is computed from the LLM call alone, never from the other half."""
    forward = _feed([_user(), _llm_start(), _thinking("start", 1.0), _thinking("end", 2.0, "r")], _ctx())
    backward = _feed([_user(), _llm_start(), _thinking("end", 2.0, "r"), _thinking("start", 1.0)], _ctx())

    def strip(out: list[dict]) -> list[dict]:
        llm_id = next(r for r in out if r["kind"] == "llm_call_start")["call_id"]
        # The LLM call id is a fresh uuid per run: compare with it named "L".
        return sorted(
            json.dumps(r, sort_keys=True).replace(llm_id, "L") for r in out if r["kind"].startswith("thinking_")
        )

    assert strip(forward) == strip(backward)


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("boundary.ingress", {"ingress_kind": "ENGINE_IO_RETURN"}),
        ("boundary.egress", {"egress_kind": "NO_OP"}),
        ("client.response", {"finish_reason": "stop"}),
        ("context.steer", {"collect_id": "u1"}),
        ("hitl.request", {"question": "ok?"}),
        ("hitl.resolve", {"resolution": "approved"}),
        ("governance.decision", {"decision": "allow", "policy_name": "p"}),
    ],
)
def test_kernel_records_name_their_owner(kind: str, payload: dict) -> None:
    # With no enclosing call named by the kernel, the owner is the agent visit that emitted it.
    out = _feed([_user("exec-root"), _boundary(kind, payload, cid=0)], _ctx())
    rec = out[-1]
    assert rec["parent_call_id"] == "exec-root", rec
    # The kernel's enclosing call wins when it names one.
    named = _feed([_user("exec-root"), _boundary(kind, payload, cid=0, parent_call_id="llm-x")], _ctx())
    assert named[-1]["parent_call_id"] == "llm-x"


def test_context_parts_are_owned_by_their_llm_call_and_make_no_rag_query() -> None:
    segments = [{"part_id": "p1", "source": "rag:docs", "mechanism": "rag", "content": "chunk"}]
    assembled = _boundary("context.assembled", {"messages": MESSAGES, "segments": segments, "op": "LLM_CALL"})
    out = _feed([_user(), assembled], _ctx())
    part = next(r for r in out if r["kind"] == "context_part_contributed")
    assert part["parent_call_id"] == part["llm_call_id"]
    assert not [r for r in out if r["kind"].startswith("rag_query")]
    assert not [r for r in out if r.get("synthetic")]


def test_state_updates_are_instants() -> None:
    mutation = _boundary("context.mutation", {"action": "wm_append", "op": "LLM_CALL", "role": "assistant"})
    out = _feed([_user(), mutation], _ctx())
    start = next(r for r in out if r["kind"] == "state_update_start")
    end = next(r for r in out if r["kind"] == "state_update_end")
    assert start["timestamp"] == end["timestamp"]
    assert "synthetic" not in end


def test_memory_calls_name_their_store() -> None:
    out = _feed(
        [
            _user(),
            _boundary("engine.io", {"op": "MEMORY_OP"}, cid=2),
            _boundary("engine.io.return", {"op": "MEMORY_OP", "text": "Memory updated."}, cid=2),
            _boundary("engine.io", {"op": "MEMORY_OP", "memory_name": "notes", "memory_type": "episodic"}, cid=3),
        ],
        _ctx(),
    )
    calls = [r for r in out if r["kind"] in ("memory_call_start", "memory_call_end")]
    assert [(r["memory_name"], r["memory_type"]) for r in calls] == [
        ("root.working_memory", "working"),
        ("root.working_memory", "working"),
        ("notes", "episodic"),
    ]


# ── a streamed completion reports real reasoning boundaries ─────────────────


class _Clock:
    """``time.time`` that advances one second each time a stream line is read."""

    def __init__(self) -> None:
        self.now = 1000.0

    def time(self) -> float:
        return self.now


class _FakeStream:
    def __init__(self, lines: list[str], clock: _Clock) -> None:
        self._lines = lines
        self._clock = clock

    is_error = False

    def raise_for_status(self) -> None:
        return None

    async def aread(self) -> bytes:
        return b""

    async def aiter_lines(self):
        for line in self._lines:
            self._clock.now += 1.0
            yield line

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeClient:
    def __init__(self, lines: list[str], clock: _Clock) -> None:
        self._lines = lines
        self._clock = clock

    def stream(self, method: str, url: str, **_: object) -> _FakeStream:
        return _FakeStream(self._lines, self._clock)


def _sse(delta: dict) -> str:
    return "data: " + json.dumps({"choices": [{"delta": delta}]})


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str, str, float]] = []

    def record_llm_thinking(self, *, correlation_id: int, boundary: str, at: float, text: str = "") -> None:
        self.calls.append((correlation_id, boundary, text, at))


async def test_streamed_reasoning_reaches_observability_when_it_happens(monkeypatch) -> None:
    from mas.library.standard.plugins.llm.openai import OpenAILLMProvider
    from mas.runtime.engine import llm_reasoning
    from mas.runtime.engine.llm_live import LiveLlmEngine
    from mas.runtime.schema.egress import InvokeEngineIo

    clock = _Clock()
    monkeypatch.setattr(llm_reasoning, "time", clock)
    lines = [
        _sse({"role": "assistant"}),  # t=1001
        _sse({"reasoning_content": "Compare "}),  # t=1002: first reasoning delta
        _sse({"reasoning_content": "the options."}),  # t=1003
        _sse({"content": "Take "}),  # t=1004: first answer delta
        _sse({"content": "the train."}),
        "data: [DONE]",
    ]
    provider = OpenAILLMProvider(api_base="https://llm.test/v1", api_key={"secret": "openai"}, stream=True)
    monkeypatch.setattr(provider, "_get_async_client", lambda: _FakeClient(lines, clock))
    recorder = _Recorder()
    chunks: list[str] = []
    ctx = SimpleNamespace(observability=recorder, on_stream_chunk=chunks.append)
    engine = LiveLlmEngine(ctx=ctx, stream=True, use_cache=False, llm_provider=provider, use_tool_loop=False)
    monkeypatch.setenv("FAKE_KEY", "k")

    async def _messages(tools=None):
        return list(MESSAGES)

    monkeypatch.setattr(engine, "_abuild_messages", _messages)

    ret = await engine.ainvoke(InvokeEngineIo(correlation_id=7, op="LLM_CALL"))

    assert ret.text == "Take the train."
    assert chunks == ["Take ", "the train."]  # reasoning never reaches the answer stream
    assert recorder.calls == [
        (7, "start", "", 1002.0),
        (7, "end", "Compare the options.", 1004.0),
    ]


def test_a_whole_response_with_reasoning_is_a_zero_length_interval(monkeypatch) -> None:
    from mas.library.standard.plugins.llm.openai import OpenAILLMProvider
    from mas.runtime.engine.llm_reasoning import ReasoningSettings, ReasoningStream

    events: list[tuple[str, str, float]] = []
    stream = ReasoningStream(lambda b, t, at: events.append((b, t, at)), clock=lambda: 50.0)
    data = {"choices": [{"message": {"content": "ok", "reasoning_content": "because"}, "finish_reason": "stop"}]}
    message = OpenAILLMProvider._message_from_completion(data, ReasoningSettings(), stream)
    assert message["content"] == "ok" and "reasoning_content" not in message
    assert events == [("start", "", 50.0), ("end", "because", 50.0)]


def test_a_reasoning_token_count_alone_is_no_interval() -> None:
    from mas.library.standard.plugins.llm.openai import OpenAILLMProvider
    from mas.runtime.engine.llm_reasoning import ReasoningSettings, ReasoningStream

    events: list[str] = []
    stream = ReasoningStream(lambda b, t, at: events.append(b))
    data = {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"completion_tokens_details": {"reasoning_tokens": 40}},
    }
    OpenAILLMProvider._message_from_completion(data, ReasoningSettings(), stream)
    assert events == []
