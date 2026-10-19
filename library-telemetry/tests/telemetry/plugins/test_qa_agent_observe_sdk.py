#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Live observe-sdk JSON plugin on the Tutorial 1 qa-agent trace.

Mirrors ``mas-ctl chat … -o library-telemetry/overlays/observability-otel-json.yaml``:
the otel plugin, ``converter_profile=observe_sdk``, spans on disk as JSONL.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mas.library.telemetry.conversion import semconv
from tests.conftest import requires_otel

PACKAGE_ROOT = Path(__file__).resolve().parents[3]
QA_EVENTS = PACKAGE_ROOT / "examples" / "qa-agent" / "events.jsonl"


def _load_events(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _load_spans(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _attrs(span: dict) -> dict:
    return dict(span.get("attributes") or {})


def _name(span: dict) -> str:
    return str(span.get("name") or "")


@requires_otel
def test_qa_agent_live_plugin_emits_session_end_and_graph(tmp_path: Path) -> None:
    if not QA_EVENTS.is_file():
        pytest.skip(f"missing {QA_EVENTS}")

    from mas.library.standard.lib.observability.native.transform import TransformContext
    from mas.library.telemetry.plugins.otel_plugin import create_otel_plugin

    spans_path = tmp_path / "traces" / "otel_sdk_spans.jsonl"
    plugin = create_otel_plugin(
        spans_path=spans_path,
        context=TransformContext(agent_id="qa-agent", run_id=""),
        app_name="qa-agent",
        converter_profile="observe_sdk",
    )
    events = _load_events(QA_EVENTS)
    for rec in sorted(
        events,
        key=lambda ev: (
            float(ev["timestamp"])
            if isinstance(ev.get("timestamp"), (int, float))
            else float("inf")
        ),
    ):
        assert plugin.converter is not None
        plugin.converter.process_event(rec)
    plugin.close()

    spans = _load_spans(spans_path)
    names = [_name(s) for s in spans]
    starts = [s for s in spans if _name(s) == semconv.SPAN_NAME_SESSION_START]
    ends = [s for s in spans if _name(s) == semconv.SPAN_NAME_SESSION_END]
    graphs = [s for s in spans if _name(s).endswith(f".{semconv.SPAN_SUFFIX_GRAPH}")]

    assert names.count("session.start") == 1
    assert names.count("session.end") == 1
    assert graphs, names
    assert graphs[0]["name"] == "qa-agent.graph"
    assert not any("/" in n for n in names)

    start_attrs = _attrs(starts[0])
    end_attrs = _attrs(ends[0])
    assert start_attrs.get("session.started_at")
    assert end_attrs.get("session.ended_at")
    assert start_attrs.get(semconv.SESSION_ID) == end_attrs.get(semconv.SESSION_ID)
    assert str(start_attrs.get(semconv.SESSION_ID) or "").startswith("qa-agent_")
    assert start_attrs.get("application.id") == "qa-agent"
    assert end_attrs.get(semconv.IOA_WORKFLOW_NAME) == "qa-agent"
    assert start_attrs.get("mas.boundary") == "Session"
    assert end_attrs.get("mas.boundary") == "Session"

    graph_attrs = _attrs(graphs[0])
    assert graph_attrs.get(semconv.IOA_SPAN_KIND) == "graph"
    assert graph_attrs.get("mas.boundary") == "Graph"
    assert graph_attrs.get(semconv.APPLICATION_ID) == "qa-agent"
    assert graph_attrs.get(semconv.IOA_ENTITY_NAME) == "qa-agent"
    payload = json.loads(graph_attrs[semconv.GEN_AI_IOA_GRAPH])
    assert isinstance(payload.get("nodes"), dict)
    assert "qa-agent" in payload["nodes"]
    assert "mas" not in payload["nodes"]
    assert graphs[0].get("parent_id") in (None, "")

    traces = {
        (s.get("context") or {}).get("trace_id")
        for s in (*starts, *ends, *graphs)
        if (s.get("context") or {}).get("trace_id")
    }
    assert len(traces) == 1, traces
