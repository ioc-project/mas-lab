#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for library-standard observability projection."""

from __future__ import annotations

import json

from mas.library.standard.lib.observability.emit import JsonlFileEmitter
from mas.library.standard.lib.observability.native.envelope import stamp_envelope_fields
from mas.library.standard.plugins.observability.native_plugin import NativeObservabilityPlugin
from mas.library.standard.lib.observability.native.transform import NativeObservabilityTransform, TransformContext
from mas.runtime.boundary.obs.transition import TransitionEvent


def test_stamp_envelope_fields_llm_call() -> None:
    rec = stamp_envelope_fields({"kind": "llm_call_start", "run_id": "run-1"})
    assert rec["block"] == "execution"
    assert rec["summand"] == "model"
    assert rec["mealy_symbol"] == "LLM_CALL"


def test_stamp_envelope_fields_emits_task_id_only_when_given() -> None:
    with_task = stamp_envelope_fields({"kind": "llm_call_start"}, task_id="task-1")
    assert with_task["task_id"] == "task-1"
    without_task = stamp_envelope_fields({"kind": "llm_call_start"})
    assert "task_id" not in without_task


def test_stamp_envelope_fields_session_id_explicit_wins_over_run_id_fallback() -> None:
    rec = stamp_envelope_fields({"kind": "llm_call_start", "run_id": "run-1"}, session_id="s-1")
    assert rec["session_id"] == "s-1"
    fallback = stamp_envelope_fields({"kind": "llm_call_start", "run_id": "run-1"})
    assert fallback["session_id"] == "session-run-1"


def test_native_plugin_emits_tool_call_with_parent(tmp_path) -> None:
    events_path = tmp_path / "events.jsonl"
    ctx = TransformContext(agent_id="planner", run_id="run-deleg", mas_call_id="mas-1", exec_call_id="exec-1")
    plugin = NativeObservabilityPlugin(
        transforms=[NativeObservabilityTransform()],
        emitters=[JsonlFileEmitter(events_path)],
        context=ctx,
        mas_id="trip-planner",
    )
    plugin.on_transition(
        TransitionEvent(
            contract_id="tool",
            mealy_symbol="TOOL_CALL",
            phase="start",
            agent_id="planner",
            run_id="run-deleg",
            correlation_id=9,
            call_id="tool-uuid-9",
            parent_call_id="exec-1",
            boundary_kind="engine.io",
            attributes={
                "op": "TOOL_CALL",
                "tool_name": "delegate_to_flights",
                "tool_arguments": {"task": "check"},
            },
        )
    )
    event = json.loads(events_path.read_text().strip())
    assert event["parent_call_id"] == "exec-1"
    assert event["call_id"] == "tool-uuid-9"


def test_native_plugin_emits_session_id_and_task_id_from_the_transition_event(tmp_path) -> None:
    """These come from the TransitionEvent itself (populated by
    ObservabilityOperator, kept in sync with KernelDriver.session_id/
    _current_task_id) — not from NativeObservabilityPlugin.session_id, which
    is an override-only field nothing sets by default."""
    events_path = tmp_path / "events.jsonl"
    ctx = TransformContext(agent_id="planner", run_id="run-1")
    plugin = NativeObservabilityPlugin(
        transforms=[NativeObservabilityTransform()],
        emitters=[JsonlFileEmitter(events_path)],
        context=ctx,
    )
    plugin.on_transition(
        TransitionEvent(
            contract_id="tool",
            mealy_symbol="TOOL_CALL",
            phase="start",
            agent_id="planner",
            run_id="run-1",
            session_id="live-session-id",
            task_id="live-task-id",
            correlation_id=1,
            boundary_kind="engine.io",
            attributes={"op": "TOOL_CALL", "tool_name": "lookup_schedule"},
        )
    )
    event = json.loads(events_path.read_text().strip())
    assert event["session_id"] == "live-session-id"
    assert event["task_id"] == "live-task-id"


def test_native_plugin_override_field_only_used_when_event_carries_none(tmp_path) -> None:
    events_path = tmp_path / "events.jsonl"
    plugin = NativeObservabilityPlugin(
        transforms=[NativeObservabilityTransform()],
        emitters=[JsonlFileEmitter(events_path)],
        context=TransformContext(agent_id="planner", run_id="run-1"),
        session_id="override-session-id",
    )
    plugin.on_transition(
        TransitionEvent(
            contract_id="tool",
            mealy_symbol="TOOL_CALL",
            phase="start",
            agent_id="planner",
            run_id="run-1",
            correlation_id=1,
            boundary_kind="engine.io",
            attributes={"op": "TOOL_CALL", "tool_name": "lookup_schedule"},
        )
    )
    event = json.loads(events_path.read_text().strip())
    assert event["session_id"] == "override-session-id"


def test_session_execution_start_parents_to_mas_call(tmp_path) -> None:
    events_path = tmp_path / "events.jsonl"
    ctx = TransformContext(agent_id="planner", run_id="run-1", turn_id="t1", mas_call_id="mas-root")
    plugin = NativeObservabilityPlugin(
        transforms=[NativeObservabilityTransform()],
        emitters=[JsonlFileEmitter(events_path)],
        context=ctx,
    )
    plugin.on_transition(
        TransitionEvent(
            contract_id="orchestrator",
            mealy_symbol="user_input",
            phase="event",
            agent_id="planner",
            run_id="run-1",
            boundary_kind="session",
            attributes={"text": "hello", "call_id": f"{ctx.turn_id}-exec"},
        )
    )
    lines = [json.loads(line) for line in events_path.read_text().splitlines()]
    assert lines[0]["kind"] == "execution_start"
    assert lines[0]["parent_call_id"] == "mas-root"


def test_native_plugin_emits_tool_call(tmp_path) -> None:
    events_path = tmp_path / "events.jsonl"
    plugin = NativeObservabilityPlugin(
        transforms=[NativeObservabilityTransform()],
        emitters=[JsonlFileEmitter(events_path)],
        context=TransformContext(agent_id="planner", run_id="run-1"),
        mas_id="trip-planner",
    )
    plugin.on_transition(
        TransitionEvent(
            contract_id="tool",
            mealy_symbol="TOOL_CALL",
            phase="start",
            agent_id="planner",
            run_id="run-1",
            correlation_id=3,
            boundary_kind="engine.io",
            attributes={
                "op": "TOOL_CALL",
                "tool_name": "delegate_to_flights",
                "tool_arguments": {"task": "check"},
            },
        )
    )
    event = json.loads(events_path.read_text().strip())
    assert event["kind"] == "tool_call_start"
    assert event["tool_name"] == "delegate_to_flights"
    assert event["mas_id"] == "trip-planner"
    assert event["app_name"] == "trip-planner"


def test_native_plugin_publishes_projected_records_on_event_stream(tmp_path) -> None:
    from mas.runtime.boundary.obs.event_stream import EventStream, reset_event_stream, set_event_stream

    events_path = tmp_path / "events.jsonl"
    stream = EventStream()
    seen: list[dict] = []
    stream.subscribe(seen.append)
    token = set_event_stream(stream)
    try:
        plugin = NativeObservabilityPlugin(
            transforms=[NativeObservabilityTransform()],
            emitters=[JsonlFileEmitter(events_path)],
            context=TransformContext(agent_id="planner", run_id="run-1"),
        )
        plugin.on_transition(
            TransitionEvent(
                contract_id="tool",
                mealy_symbol="TOOL_CALL",
                phase="start",
                agent_id="planner",
                run_id="run-1",
                correlation_id=3,
                boundary_kind="engine.io",
                attributes={"op": "TOOL_CALL", "tool_name": "search"},
            )
        )
    finally:
        reset_event_stream(token)
    assert seen
    assert seen[0]["kind"] == "tool_call_start"
    assert seen[0]["tool_name"] == "search"


def test_jsonl_file_emitter_skips_envelope_kinds_and_truncates(tmp_path) -> None:
    dest = tmp_path / "traces" / "events.jsonl"
    dest.parent.mkdir(parents=True)
    dest.write_text('{"kind":"stale"}\n', encoding="utf-8")
    emitter = JsonlFileEmitter(dest)
    emitter.emit({"kind": "llm_call_end", "n": 1})
    emitter.emit({"kind": "control.structure", "offset": 0})
    lines = [ln for ln in dest.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1
    assert "llm_call_end" in lines[0]
    assert "stale" not in dest.read_text(encoding="utf-8")
