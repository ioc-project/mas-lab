#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""llm_call_start is the request; llm_call_end is the return."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mas.library.standard.lib.observability.emit import JsonlFileEmitter
from mas.library.standard.lib.observability.native.transform import NativeObservabilityTransform, TransformContext
from mas.library.standard.plugins.observability.native_plugin import NativeObservabilityPlugin
from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.engine.simulated import SimulatedEngine
from mas.runtime.schema.ingress import EngineIoReturn


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _native_plugin(path: Path) -> NativeObservabilityPlugin:
    return NativeObservabilityPlugin(
        transforms=[NativeObservabilityTransform()],
        emitters=[JsonlFileEmitter(path)],
        context=TransformContext(agent_id="planner", run_id="run-llm-interval"),
    )


def _run_one_call(tmp_path: Path, *, asked: int, sent: int | None) -> tuple[dict, dict]:
    events_path = tmp_path / "events.jsonl"
    engine = SimulatedEngine(
        model="openai/gpt-4o",
        llm_messages=[{"role": "user", "content": "hi"}],
        max_tokens=asked,
        script={
            1: EngineIoReturn(
                correlation_id=1,
                response_kind="MODEL_TEXT",
                next_step="STOP",
                text="ok",
                finish_reason="stop",
                max_tokens=sent,
            )
        },
    )
    inst = RuntimeInstance.from_parts(engine=engine)
    obs = _native_plugin(events_path)
    inst.driver.observability.subscribe(obs)
    inst.capture_session_baseline()
    inst.run_user_text("hi")
    obs.flush()
    obs.close()
    records = _records(events_path)
    (start,) = [r for r in records if r.get("kind") == "llm_call_start"]
    (end,) = [r for r in records if r.get("kind") == "llm_call_end"]
    return start, end


def test_end_repeats_no_cap_when_the_cap_sent_is_the_one_asked(tmp_path: Path) -> None:
    start, end = _run_one_call(tmp_path, asked=300, sent=300)
    assert start["max_tokens"] == 300
    assert "max_tokens" not in end


def test_end_reports_the_cap_actually_sent_when_it_differs_from_the_one_asked(tmp_path: Path) -> None:
    """Fitted to the prompt or escalated after a truncation: start says 300, 150 was sent."""
    start, end = _run_one_call(tmp_path, asked=300, sent=150)
    assert start["max_tokens"] == 300
    assert end["max_tokens"] == 150


def test_llm_call_start_carries_request_end_does_not(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    engine = SimulatedEngine(
        model="bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0",
        llm_messages=[{"role": "user", "content": "renew the deal"}],
        llm_tools=[{"type": "function", "function": {"name": "lookup_quote"}}],
        max_tokens=256,
        temperature=0.2,
        script={
            1: EngineIoReturn(
                correlation_id=1,
                response_kind="MODEL_TEXT",
                next_step="STOP",
                text="ok",
                finish_reason="stop",
                usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
                offered_tools=["lookup_quote"],
                max_tokens=512,
            )
        },
    )
    inst = RuntimeInstance.from_parts(engine=engine)
    obs = _native_plugin(events_path)
    inst.driver.observability.subscribe(obs)
    inst.capture_session_baseline()
    inst.run_user_text("renew the deal")
    obs.flush()
    obs.close()

    records = _records(events_path)
    starts = [r for r in records if r.get("kind") == "llm_call_start"]
    ends = [r for r in records if r.get("kind") == "llm_call_end"]
    assert len(starts) == 1, starts
    assert len(ends) == 1, ends
    start, end = starts[0], ends[0]
    assert start["model"] == "bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert start["messages"] == [{"role": "user", "content": "renew the deal"}]
    assert start["tools"] == ["lookup_quote"]
    assert start["max_tokens"] == 256
    assert start["temperature"] == 0.2
    assert "model" not in end
    assert "tools" not in end
    assert end["max_tokens"] == 512  # not the 256 asked: reported because it differs
    assert "messages" not in end
    assert end["output"] == "ok"
    assert end["finish_reason"] == "stop"
    assert end["tokens_used"]["total_tokens"] == 12


def test_llm_call_end_carries_tool_calls_when_model_asks_for_tools(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    engine = SimulatedEngine(
        model="openai/gpt-4o",
        llm_messages=[{"role": "user", "content": "look it up"}],
        llm_tools=[{"type": "function", "function": {"name": "search"}}],
        llm_next_step=lambda cid: "TOOL_CALL" if cid == 1 else "STOP",
        llm_tool_intent=lambda cid: ("search", {"q": "acme"}),
        stop_text="done",
    )
    inst = RuntimeInstance.from_parts(engine=engine)
    obs = _native_plugin(events_path)
    inst.driver.observability.subscribe(obs)
    inst.capture_session_baseline()
    inst.run_user_text("look it up")
    obs.flush()
    obs.close()

    records = _records(events_path)
    llm_ends = [r for r in records if r.get("kind") == "llm_call_end"]
    assert llm_ends
    first = llm_ends[0]
    assert "model" not in first
    assert first["tool_calls"] == [{"toolName": "search", "arguments": {"q": "acme"}}]
    assert first["next_step"] == "TOOL_CALL"


def test_contract_call_id_in_jsonl_is_not_the_llm_call_id(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    engine = SimulatedEngine(
        model="openai/gpt-4o",
        llm_messages=[{"role": "user", "content": "hi"}],
        script={
            1: EngineIoReturn(
                correlation_id=1,
                response_kind="MODEL_TEXT",
                next_step="STOP",
                text="hi",
            )
        },
    )
    inst = RuntimeInstance.from_parts(engine=engine)
    obs = _native_plugin(events_path)
    inst.driver.observability.subscribe(obs)
    inst.capture_session_baseline()
    inst.run_user_text("hi")
    obs.flush()
    obs.close()

    records = _records(events_path)
    llm = next(r for r in records if r.get("kind") == "llm_call_start")
    wrapper = next(r for r in records if r.get("kind") == "contract_call_start")
    assert llm["call_id"]
    assert wrapper["call_id"]
    assert llm["call_id"] != wrapper["call_id"]


def _run_with_step_cap(tmp_path: Path, cap: int) -> list[dict]:
    events_path = tmp_path / "events.jsonl"
    engine = SimulatedEngine(
        model="openai/gpt-4o",
        llm_messages=[{"role": "user", "content": "x"}],
        llm_next_step=lambda cid: "TOOL_CALL" if cid == 1 else "STOP",
        llm_tool_intent=lambda cid: ("search", {"q": "a"}),
        stop_text="done",
    )
    inst = RuntimeInstance.from_parts(engine=engine)
    inst.driver.max_auto_steps = cap
    obs = _native_plugin(events_path)
    inst.driver.observability.subscribe(obs)
    inst.capture_session_baseline()
    inst.run_user_text("x")
    obs.flush()
    obs.close()
    return _records(events_path)


def test_a_call_in_flight_when_the_step_budget_runs_out_gets_an_error_end(tmp_path: Path) -> None:
    """The run is cut off with the model's reply still queued: its interval must still close."""
    records = _run_with_step_cap(tmp_path, cap=2)
    (start,) = [r for r in records if r.get("kind") == "llm_call_start"]
    (end,) = [r for r in records if r.get("kind") == "llm_call_end"]
    assert end["call_id"] == start["call_id"]
    assert end["status"] == "error"
    assert end["output"] == "step budget exhausted"


def test_a_run_inside_its_step_budget_has_no_error_end(tmp_path: Path) -> None:
    records = _run_with_step_cap(tmp_path, cap=50)
    ends = [r for r in records if r.get("kind") == "llm_call_end"]
    assert ends and all("status" not in r for r in ends)


def test_abandoning_a_call_pops_the_envelope_frame() -> None:
    """A cut-off call's envelope frame is popped, so the next call is not its child."""
    from mas.runtime.boundary.obs.observability_plugin import ObservabilityPlugin
    from mas.runtime.boundary.obs.operator import ObservabilityOperator
    from mas.runtime.schema.observability import ObsPhase

    class _Sink(ObservabilityPlugin):
        def on_transition(self, event) -> None:
            pass

    op = ObservabilityOperator()
    op.subscribe(_Sink())
    op.record_envelope_activity(
        symbol="CONTRACT_START",
        activity="contract_call",
        boundary="start",
        phase=ObsPhase.REQUEST,
        correlation_id=7,
        payload={"op": "LLM_CALL"},
    )
    op.record_engine_io(correlation_id=7, op="LLM_CALL")
    assert op._frames.top is not None
    (end,) = op.abandon_open_engine_calls("step budget exhausted")
    assert end.payload["response_kind"] == "ERROR"
    assert op._frames.top is None
    assert not op._open_engine_calls


def test_a_run_that_raises_mid_step_closes_the_call_it_left_open(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    engine = SimulatedEngine(model="openai/gpt-4o", llm_messages=[{"role": "user", "content": "x"}])
    inst = RuntimeInstance.from_parts(engine=engine)
    obs = _native_plugin(events_path)
    inst.driver.observability.subscribe(obs)
    inst.capture_session_baseline()

    def boom(*_args, **_kwargs):
        raise RuntimeError("recorder failed")

    inst.driver._record_engine_return = boom
    with pytest.raises(RuntimeError, match="recorder failed"):
        inst.run_user_text("x")
    obs.flush()
    obs.close()

    records = _records(events_path)
    (start,) = [r for r in records if r.get("kind") == "llm_call_start"]
    (end,) = [r for r in records if r.get("kind") == "llm_call_end"]
    assert end["call_id"] == start["call_id"]
    assert end["status"] == "error"
    assert end["output"] == "aborted: RuntimeError"
    assert not inst.driver.observability._open_engine_calls


def test_abandoning_closes_calls_on_the_bound_operator_as_well(monkeypatch) -> None:
    from mas.runtime.boundary.obs.operator import ObservabilityOperator

    own, bound = ObservabilityOperator(), ObservabilityOperator()
    bound.record_engine_io(correlation_id=3, op="LLM_CALL")
    own.record_engine_io(correlation_id=4, op="TOOL_CALL")
    inst = RuntimeInstance.from_parts(engine=SimulatedEngine(model="openai/gpt-4o"))
    inst.driver.observability = own
    monkeypatch.setattr("mas.runtime.boundary.gov.telemetry.get_bound_observability", lambda: bound)

    inst.driver._abandon_in_flight_calls("cut off")

    assert not own._open_engine_calls
    assert not bound._open_engine_calls
