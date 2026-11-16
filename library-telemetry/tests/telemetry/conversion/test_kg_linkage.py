#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Every node of a run's KG is linked, through both folds.

A run (simulated model with a reasoning interval and a tool call, every
observability category on) is recorded live, then folded twice: the native
fold (``mas.library.kg``, ``categories="all"``) and the OTel path (this
converter, then SCOPE norm). In both graphs every node but the Session has a
containment / ownership / ``annotates`` edge, and the ontology's
``CallContainedShape`` holds.
"""

from __future__ import annotations

import collections
import json
import time
from pathlib import Path

import pytest
from mas.library.telemetry.conversion.replay import replay_events_file

_DATAFLOW = {
    "hasState",
    "hasInitialState",
    "hasFinalState",
    "inputTo",
    "leadsTo",
    "correspondsToTransition",
    "representsExecution",
}
_ANNOTATIONS = {"CallAnnotation", "GovernanceEvent", "ContextContribution"}
_ALL_LAYERS = {
    "structure": True,
    "execution": True,
    "semantic": True,
    "provenance": True,
    "governance": True,
    "annotation": True,
}


def _record_run(tmp_path: Path) -> Path:
    from mas.library.standard.lib.observability.emit import JsonlFileEmitter
    from mas.library.standard.lib.observability.native.transform import (
        NativeObservabilityTransform,
        TransformContext,
    )
    from mas.library.standard.plugins.observability.native_plugin import NativeObservabilityPlugin
    from mas.runtime.boundary.obs.plugins import ObsPluginSet, attach_observability_plugin_set
    from mas.runtime.driver.instance import RuntimeInstance
    from mas.runtime.engine.simulated import SimulatedEngine
    from mas.runtime.schema.ingress import EngineIoReturn

    class _Engine(SimulatedEngine):
        observability = None

        async def ainvoke(self, io):  # type: ignore[override]
            if io.op == "LLM_CALL" and self.observability is not None:
                record = self.observability.record_llm_thinking
                record(correlation_id=io.correlation_id, boundary="start", at=time.time())
                record(correlation_id=io.correlation_id, boundary="end", at=time.time(), text="think")
            return await super().ainvoke(io)

    events_path = tmp_path / "events.jsonl"
    engine = _Engine(
        model="openai/o4-mini",
        llm_messages=[{"role": "user", "content": "hi"}],
        script={
            1: EngineIoReturn(
                correlation_id=1,
                response_kind="MODEL_TEXT",
                next_step="TOOL_CALL",
                tool_name="lookup",
                tool_arguments={"q": "x"},
            ),
            2: EngineIoReturn(correlation_id=2, response_kind="TOOL_RESULT", next_step="LLM_CALL", text="found"),
            3: EngineIoReturn(correlation_id=3, response_kind="MODEL_TEXT", next_step="STOP", text="done"),
        },
    )
    inst = RuntimeInstance.from_parts(engine=engine)
    engine.observability = inst.driver.observability
    plugin = NativeObservabilityPlugin(
        transforms=[NativeObservabilityTransform()],
        emitters=[JsonlFileEmitter(events_path)],
        context=TransformContext(agent_id="planner", run_id="linkage"),
    )
    plugin_set = ObsPluginSet(plugins=[plugin])
    attach_observability_plugin_set(plugin_set, inst, agent_id="planner")
    inst.capture_session_baseline()
    inst.run_user_text("hi")
    plugin_set.close()
    return events_path


def _unlinked(nodes: list[dict], edges: list[dict]) -> dict[str, int]:
    """Per node type, the nodes with no containment, ownership or ``annotates`` edge."""
    incoming: dict[str, set[str]] = collections.defaultdict(set)
    outgoing: dict[str, set[str]] = collections.defaultdict(set)
    for edge in edges:
        outgoing[edge["from_id"]].add(edge["edge_type"])
        incoming[edge["to_id"]].add(edge["edge_type"])
    missing: collections.Counter = collections.Counter()
    for node in nodes:
        kind, node_id = node["node_type"], node["id"]
        if kind in ("Session", "MASCall"):
            continue
        if kind in _ANNOTATIONS:
            linked = "annotates" in outgoing[node_id]
        elif kind.endswith("Call") or kind in ("RAGQuery", "ParallelGroup", "Branch"):
            linked = any(e.startswith(("has", "contains")) and e not in _DATAFLOW for e in incoming[node_id])
        elif kind == "Worker":
            linked = "runsOn" in incoming[node_id]
        else:
            linked = bool((incoming[node_id] | outgoing[node_id]) - set())
        if not linked:
            missing[kind] += 1
    return dict(missing)


def _call_contained_violations(nodes: list[dict], edges: list[dict]) -> list[str]:
    from norm.verifier import run_shacl_validation
    from scope_ontology import get_ontology_path

    _passed, violations, _warnings = run_shacl_validation(
        nodes, edges, Path(get_ontology_path()), "linkage", layers=["execution"]
    )
    return [str(v) for v in violations if "CallContainedShape" in str(v)]


def _native_graph(events_path: Path) -> tuple[list[dict], list[dict]]:
    from mas.library.kg.steps.normalize import run_normalize

    doc = run_normalize(events_path, run_id="linkage", categories="all").to_doc()
    return doc["nodes"], doc["edges"]


def _otel_graph(events_path: Path, tmp_path: Path) -> tuple[list[dict], list[dict]]:
    from mas.library.kg.observability.otel_via_norm import to_clickhouse_spans
    from norm.ioa_observe.build import build_kg

    out = tmp_path / "spans.jsonl"
    replay_events_file(
        events_path,
        out,
        app_name="linkage",
        converter_profile="observe_sdk",
        export_layers=_ALL_LAYERS,
        extensions=True,
    )
    spans = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    return build_kg(to_clickhouse_spans(spans))


def test_every_native_record_names_a_call_of_the_run(tmp_path: Path) -> None:
    events = [json.loads(line) for line in _record_run(tmp_path).read_text().splitlines() if line.strip()]
    calls = {e["call_id"] for e in events if e["kind"].endswith("_start") and e.get("call_id")}
    unowned = collections.Counter(
        e["kind"]
        for e in events
        if e["kind"] != "mas_call_start" and e.get("call_id") not in calls and e.get("parent_call_id") not in calls
    )
    assert not unowned, unowned
    assert {"thinking_start", "thinking_end", "tool_call_start", "governance_decision"} <= {e["kind"] for e in events}


@pytest.mark.scope_next
def test_otel_fold_links_every_node(tmp_path: Path) -> None:
    nodes, edges = _otel_graph(_record_run(tmp_path), tmp_path)
    assert "ThinkingCall" in {n["node_type"] for n in nodes}
    assert _unlinked(nodes, edges) == {}
    assert _call_contained_violations(nodes, edges) == []


@pytest.mark.scope_next
def test_native_fold_links_every_node(tmp_path: Path) -> None:
    nodes, edges = _native_graph(_record_run(tmp_path))
    assert _unlinked(nodes, edges) == {}
    assert _call_contained_violations(nodes, edges) == []
