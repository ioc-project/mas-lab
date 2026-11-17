#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Native pipeline tests for ``normalize`` / ``build_kg_document``."""

from __future__ import annotations

from pathlib import Path

from mas.library.kg.pipeline import build_kg_document, load_events_jsonl, normalize


def test_normalize_native_events_jsonl(tmp_path: Path) -> None:
    events = [
        {
            "kind": "execution_start",
            "timestamp": 1.0,
            "run_id": "pipe-1",
            "call_id": "c1",
            "agent_id": "planner",
            "input": "hello",
        },
        {
            "kind": "execution_end",
            "timestamp": 2.0,
            "run_id": "pipe-1",
            "call_id": "c1",
            "agent_id": "planner",
            "status": "success",
            "output": "done",
        },
    ]
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(__import__("json").dumps(e) for e in events), encoding="utf-8")
    nodes, edges = normalize(str(path))
    types = {n.get("node_type") for n in nodes}
    assert "AgentCall" in types
    assert "Session" in types
    assert edges


def test_build_kg_document_native_trip_planner_fixture() -> None:
    fixtures = Path(__file__).resolve().parents[2] / "examples" / "trip-planner" / "events.jsonl"
    events = load_events_jsonl(fixtures)
    doc = build_kg_document(events, run_id="trip-planner-demo", categories="annotations")
    types = {n.get("node_type") for n in doc["nodes"]}
    assert "MASCall" in types or "AgentCall" in types
    assert doc["metadata"]["nodes"] == len(doc["nodes"])


def test_normalize_detects_otel_clickhouse_shape(monkeypatch) -> None:
    captured: list[list] = []

    def _fake_normalize(spans):
        captured.append(list(spans))
        return ([{"node_type": "Session", "id": "s"}], [])

    import sys
    import types

    fake_norm = types.ModuleType("norm")
    fake_normalizer = types.ModuleType("norm.normalizer")
    fake_normalizer.normalize = _fake_normalize
    monkeypatch.setitem(sys.modules, "norm", fake_norm)
    monkeypatch.setitem(sys.modules, "norm.normalizer", fake_normalizer)
    nodes, _edges = normalize(
        [{"SpanName": "planner.agent", "SpanAttributes": {"agent_id": "p"}, "SpanId": "1"}]
    )
    assert captured
    assert nodes[0]["node_type"] == "Session"
