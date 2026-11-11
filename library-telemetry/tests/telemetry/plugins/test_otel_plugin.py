#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest
from mas.library.standard.lib.observability.native.transform import (
    NativeObservabilityTransform,
    TransformContext,
)
from mas.library.telemetry.plugins.otel_plugin import OtelObservabilityPlugin
from mas.runtime.boundary.obs.transition import TransitionEvent


class _DummyConverter:
    def __init__(self) -> None:
        self.processed: list[dict] = []
        self.graph_calls: list[tuple[dict, dict]] = []
        self.flush_count = 0
        self._app_name = "sample-app"

    def process_event(self, rec: dict) -> None:
        self.processed.append(rec)

    def emit_graph_span(self, topology: dict, **kwargs) -> None:
        self.graph_calls.append((topology, kwargs))

    def flush_open_spans(self) -> None:
        self.flush_count += 1

    def finish(self, *, emit_graph: bool | None = None) -> None:
        from mas.library.telemetry.conversion.topology import build_topology

        if self.processed and emit_graph is not False:
            self.emit_graph_span(build_topology(self.processed), app_name=self._app_name)
        self.flush_open_spans()


def test_flush_emits_graph_span_from_projected_events(monkeypatch) -> None:
    records = [
        {
            "kind": "system_specification",
            "timestamp": 1.0,
            "agents": [{"id": "planner"}, {"id": "worker"}],
        },
        {
            "kind": "routing",
            "timestamp": 2.0,
            "source_agent_id": "planner",
            "target_agent_id": "worker",
            "call_id": "mas-1",
        },
    ]

    def fake_project_transition(*_args, **_kwargs):
        return records

    monkeypatch.setattr(
        "mas.library.telemetry.plugins.otel_plugin.project_transition",
        fake_project_transition,
    )

    plugin = OtelObservabilityPlugin(
        converter=_DummyConverter(),
        context=TransformContext(agent_id="planner", run_id="run-1"),
        mas_id="sample-app",
        session_id="session-1",
    )

    plugin.on_transition(
        TransitionEvent(
            contract_id="orchestrator",
            mealy_symbol="user_input",
            phase="event",
            agent_id="planner",
            run_id="run-1",
            boundary_kind="session",
        )
    )
    plugin.flush()

    dummy = plugin.converter
    assert dummy is not None
    assert dummy.processed == records
    assert dummy.flush_count == 1
    assert len(dummy.graph_calls) == 1

    topology, kwargs = dummy.graph_calls[0]
    assert set(topology["nodes"]) == {"planner", "worker"}
    assert kwargs["app_name"] == "sample-app"


def test_on_transition_forwards_event_session_id_and_task_id_to_project_transition() -> None:
    converter = _DummyConverter()
    plugin = OtelObservabilityPlugin(
        converter=converter,
        native_transform=NativeObservabilityTransform(),
        context=TransformContext(agent_id="planner", run_id="run-1"),
        mas_id="sample-app",
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

    assert converter.processed, "expected at least one projected record"
    assert all(rec.get("session_id") == "live-session-id" for rec in converter.processed)
    assert all(rec.get("task_id") == "live-task-id" for rec in converter.processed)


def test_on_transition_falls_back_to_override_field_when_event_has_none() -> None:
    converter = _DummyConverter()
    plugin = OtelObservabilityPlugin(
        converter=converter,
        native_transform=NativeObservabilityTransform(),
        context=TransformContext(agent_id="planner", run_id="run-1"),
        mas_id="sample-app",
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

    assert converter.processed
    assert all(rec.get("session_id") == "override-session-id" for rec in converter.processed)


def test_shared_plugin_preserves_agents_with_reused_call_numbers() -> None:
    from concurrent.futures import ThreadPoolExecutor

    converter = _DummyConverter()
    plugin = OtelObservabilityPlugin(converter=converter, context=TransformContext(agent_id="planner"))

    def emit(agent):
        for kind, phase in (("engine.io", "start"), ("engine.io.return", "end")):
            plugin.on_transition(
                TransitionEvent(
                    contract_id="tool",
                    mealy_symbol="TOOL_CALL",
                    phase=phase,
                    agent_id=agent,
                    session_id="conversation",
                    correlation_id=1,
                    boundary_kind=kind,
                    attributes={"op": "TOOL_CALL", "tool_name": "lookup_record"},
                )
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(emit, ["reader", "writer"]))
    starts = [record for record in converter.processed if record["kind"] == "tool_call_start"]
    ends = [record for record in converter.processed if record["kind"] == "tool_call_end"]
    assert {record["agent_id"] for record in starts} == {"reader", "writer"}
    assert len(starts) == len(ends) == 2
    assert {record["call_id"] for record in starts} == {record["call_id"] for record in ends}


def test_shared_run_frame_is_inherited_and_flushed_on_close() -> None:
    converter = _DummyConverter()
    plugin = OtelObservabilityPlugin(converter=converter)
    plugin.on_transition(
        TransitionEvent(
            contract_id="orchestrator",
            mealy_symbol="mas_call_start",
            phase="event",
            agent_id="mas",
            run_id="run",
            boundary_kind="session",
            attributes={"call_id": "mas-call"},
        )
    )
    plugin.on_transition(
        TransitionEvent(
            contract_id="orchestrator",
            mealy_symbol="user_input",
            phase="event",
            agent_id="worker",
            session_id="conversation",
            boundary_kind="session",
            attributes={"text": "request"},
        )
    )
    assert plugin._contexts[("worker", "conversation")].mas_call_id == "mas-call"
    plugin.close()
    assert converter.flush_count == 1
    empty = OtelObservabilityPlugin()
    empty.on_transition(TransitionEvent(contract_id="tool", mealy_symbol="TOOL_CALL", phase="start"))
    empty.close()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("explicit_session", [False, True])
def test_runtime_turn_projection_keeps_effective_session(tmp_path, asynchronous, explicit_session):
    import asyncio

    from mas.library.telemetry.plugins.otel_plugin import create_otel_plugin
    from mas.runtime.boundary.obs.plugins import ObsPluginSet
    from mas.runtime.driver.instance import RuntimeInstance
    from mas.runtime.engine.simulated import SimulatedEngine

    plugin = create_otel_plugin(
        spans_path=tmp_path / "spans.jsonl", converter_profile="raw", app_name="test", context=TransformContext()
    )
    owner = ObsPluginSet(plugins=[plugin])
    for agent in ("reader", "writer"):
        instance = RuntimeInstance.from_parts(engine=SimulatedEngine(llm_next_step=lambda _: "STOP", stop_text=agent))
        instance.driver.agent_id = agent
        owner.subscribe_to(instance.driver.observability, agent_id=agent)
        instance.obs_plugin_set = owner
        owner.begin_run(instance.driver.observability)
        for i, session in enumerate(("A" if explicit_session else "", "B", "A")):
            kwargs = {"session_id": session, "turn_id": f"u{i}"}
            if asynchronous:
                asyncio.run(instance.arun_user_text("hello", **kwargs))
            else:
                instance.run_user_text("hello", **kwargs)
    owner.flush()
    records = plugin.converter._seen_events
    starts = [ev for ev in records if ev["kind"] == "execution_start"]
    ends = [ev for ev in records if ev["kind"] == "execution_end"]
    responses = [ev for ev in records if ev["kind"] == "user_response"]
    assert len(starts) == len(ends) == len(responses) == 6
    ends_by_call = {ev["call_id"]: ev for ev in ends}
    replies_by_parent = {ev["parent_call_id"]: ev for ev in responses}
    for start in starts:
        end = ends_by_call[start["call_id"]]
        response = replies_by_parent[start["call_id"]]
        assert start["call_id"] == end["call_id"] == response["parent_call_id"]
        assert start["session_id"] == end["session_id"] == response["session_id"]
        assert start["session_id"]
        assert response["turn_id"] == start["turn_id"]
        assert response["call_id"] != "-resp"
    owner.close()
