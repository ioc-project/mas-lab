#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Native JSONL export plugin tests."""

from __future__ import annotations

import json

from mas.library.standard.lib.observability.emit import JsonlFileEmitter
from mas.library.standard.lib.observability.native.envelope import stamp_envelope_fields
from mas.library.standard.lib.observability.native.transform import NativeObservabilityTransform, TransformContext
from mas.library.standard.plugins.observability.native_plugin import NativeObservabilityPlugin
from mas.runtime.boundary.obs.binding import ObservabilityBinding
from mas.runtime.boundary.obs.transition import TransitionEvent


def test_stamp_envelope_fields_llm_call() -> None:
    rec = stamp_envelope_fields({"kind": "llm_call_start", "run_id": "run-1"})
    assert rec["block"] == "execution"
    assert rec["summand"] == "model"
    assert rec["mealy_symbol"] == "LLM_CALL"
    assert rec["session_id"] == "session-run-1"


def test_native_jsonl_plugin_tool_call_with_arguments(tmp_path) -> None:
    events_path = tmp_path / "events.jsonl"
    ctx = TransformContext(agent_id="planner", run_id="run-deleg")
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
            boundary_kind="engine.io",
            attributes={
                "op": "TOOL_CALL",
                "tool_name": "delegate_to_flights",
                "tool_arguments": {"task": "check fares"},
                "envelope": True,
            },
        )
    )
    event = json.loads(events_path.read_text().strip())
    assert event["kind"] == "tool_call_start"
    assert event["tool_name"] == "delegate_to_flights"
    assert event["arguments"] == {"task": "check fares"}
    assert event["mas_id"] == "trip-planner"
    assert event["app_name"] == "trip-planner"
    assert event["block"] == "execution"
    assert event["summand"] == "tool"
    assert event["mealy_symbol"] == "TOOL_CALL"


def test_native_jsonl_plugin_engine_io_llm(tmp_path) -> None:
    """LLM calls reach the native plugin via the engine.io boundary (ENGINE_IO
    boundary events), not via a contract_call envelope activity — the operator
    emits both for the same call, and the envelope-activity translation was
    removed as a duplicate (see boundary_handlers.py)."""
    events_path = tmp_path / "events.jsonl"
    plugin = NativeObservabilityPlugin(
        transforms=[NativeObservabilityTransform()],
        emitters=[JsonlFileEmitter(events_path)],
        context=TransformContext(agent_id="planner", run_id="run-test"),
    )
    plugin.on_transition(
        TransitionEvent(
            contract_id="model",
            mealy_symbol="LLM_CALL",
            phase="start",
            agent_id="planner",
            run_id="run-test",
            correlation_id=5,
            boundary_kind="engine.io",
            attributes={
                "op": "LLM_CALL",
                "model": "openai/gpt-4o",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": ["search"],
                "max_tokens": 128,
                "temperature": 0.1,
            },
        )
    )
    plugin.on_transition(
        TransitionEvent(
            contract_id="model",
            mealy_symbol="LLM_CALL",
            phase="end",
            agent_id="planner",
            run_id="run-test",
            correlation_id=5,
            boundary_kind="engine.io.return",
            attributes={
                "op": "LLM_CALL",
                "text": "ok",
                "next_step": "STOP",
                "finish_reason": "stop",
                "model": "should-not-copy",
                "tools": ["search"],
                "max_tokens": 64,
                "tool_calls": [{"toolName": "search", "arguments": {"q": "x"}}],
            },
        )
    )
    lines = [json.loads(line) for line in events_path.read_text().splitlines() if line.strip()]
    start = next(e for e in lines if e["kind"] == "llm_call_start")
    end = next(e for e in lines if e["kind"] == "llm_call_end")
    assert start["model"] == "openai/gpt-4o"
    assert start["messages"] == [{"role": "user", "content": "hi"}]
    assert start["tools"] == ["search"]
    assert start["max_tokens"] == 128
    assert start["temperature"] == 0.1
    assert start["block"] == "execution"
    assert start["summand"] == "model"
    assert start["mealy_symbol"] == "LLM_CALL"
    assert "model" not in end
    assert "tools" not in end
    assert end["max_tokens"] == 64  # a different cap than the 128 asked for on start
    assert end["output"] == "ok"
    assert end["tool_calls"] == [{"toolName": "search", "arguments": {"q": "x"}}]


def test_native_jsonl_plugin_contract_call_alone_produces_no_llm_call(tmp_path) -> None:
    """A bare contract_call envelope activity (no paired engine.io event) must
    not translate to llm_call_start — that would duplicate the llm_call_start
    ENGINE_IO already produces for the same call."""
    events_path = tmp_path / "events.jsonl"
    plugin = NativeObservabilityPlugin(
        transforms=[NativeObservabilityTransform()],
        emitters=[JsonlFileEmitter(events_path)],
        context=TransformContext(agent_id="planner", run_id="run-test"),
    )
    plugin.on_transition(
        TransitionEvent(
            contract_id="model",
            mealy_symbol="LLM_CALL",
            phase="start",
            agent_id="planner",
            run_id="run-test",
            correlation_id=5,
            boundary_kind="envelope.activity",
            attributes={
                "activity": "contract_call",
                "boundary": "start",
                "op": "LLM_CALL",
            },
        )
    )
    assert not any("llm_call" in line for line in events_path.read_text().splitlines())


def test_native_from_binding_uses_mas_name(tmp_path) -> None:
    binding = ObservabilityBinding(
        plugins=["native"],
        plugin_configs={
            "native": {
                "path": str(tmp_path / "e.jsonl"),
                "app_name": "trip-planner",
            }
        },
    )
    plugin = NativeObservabilityPlugin.from_binding(binding, base_dir=tmp_path, agent_id="planner")
    assert plugin.mas_id == "trip-planner"
