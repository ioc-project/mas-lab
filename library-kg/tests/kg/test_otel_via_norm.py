#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The OTel→KG wrapper delegates to ``norm.normalize`` and has no handlers."""

from __future__ import annotations

from pathlib import Path

import pytest

otel_via_norm = pytest.importorskip("mas.library.kg.observability.otel_via_norm")


def test_wrapper_has_no_handler_logic() -> None:
    source = Path(otel_via_norm.__file__).read_text(encoding="utf-8")
    banned = (
        "IoaObserveHandler",
        "handle_agent",
        "handle_chat",
        "handle_tool",
        "convert_spans_to_events",
        "synthesize_missing_llm",
        "_BOUNDARY_DISPATCH",
        # This wrapper is a user of norm/oxp-ontology, not a patcher of
        # either: no monkey-patching their models to tolerate a mismatch
        # between them, no matter how well-intentioned or well-documented.
        "model_config",
        "model_rebuild",
    )
    for token in banned:
        assert token not in source, f"wrapper must not contain {token!r}"
    assert "from norm.normalizer import normalize" in source
    assert "def normalize_otel" in source


def test_normalize_otel_calls_norm(monkeypatch) -> None:
    captured: list[list] = []

    def _fake_normalize(spans):
        captured.append(list(spans))
        return ([{"node_type": "Session", "id": "s1"}], [])

    import sys
    import types

    fake_norm = types.ModuleType("norm")
    fake_normalizer = types.ModuleType("norm.normalizer")
    fake_normalizer.normalize = _fake_normalize
    monkeypatch.setitem(sys.modules, "norm", fake_norm)
    monkeypatch.setitem(sys.modules, "norm.normalizer", fake_normalizer)

    spans = [
        {
            "name": "planner.agent",
            "context": {"trace_id": "tt", "span_id": "ss"},
            "attributes": {"session.id": "s1", "agent_id": "planner"},
            "start_time": "2024-01-01T00:00:00Z",
            "end_time": "2024-01-01T00:00:01Z",
        }
    ]
    nodes, edges = otel_via_norm.normalize_otel(spans)
    assert captured, "expected norm.normalize to be called"
    row = captured[0][0]
    assert row["SpanName"] == "planner.agent"
    # ClickHouse's SpanId column is a fixed 16-hex-char string; sdk_span_to_
    # clickhouse zero-pads to that width (see _wire_hex), so this toy id
    # comes back padded, not bare.
    assert row["SpanId"] == "00000000000000ss"
    assert row["SpanAttributes"]["agent_id"] == "planner"
    assert nodes[0]["node_type"] == "Session"
    # align_graph adds SCOPE topology (executesSession / belongsToMAS / …)
    # on top of whatever norm returned.
    assert all(e.get("from_id") and e.get("to_id") for e in edges)


def test_clickhouse_shape_passes_through(monkeypatch) -> None:
    def _fake_normalize(spans):
        return spans, []

    import sys
    import types

    fake_norm = types.ModuleType("norm")
    fake_normalizer = types.ModuleType("norm.normalizer")
    fake_normalizer.normalize = _fake_normalize
    monkeypatch.setitem(sys.modules, "norm", fake_norm)
    monkeypatch.setitem(sys.modules, "norm.normalizer", fake_normalizer)
    spans = [
        {
            "SpanName": "planner.agent",
            "SpanAttributes": {"agent_id": "planner"},
            "SpanId": "abc",
            "ServiceName": "demo",
            "Timestamp": "2024-01-01 00:00:00.000000000",
            "Duration": 1000,
            "ParentSpanId": "",
        }
    ]
    nodes, _edges = otel_via_norm.normalize_otel(spans)
    assert nodes[0]["SpanName"] == "planner.agent"


def test_sdk_span_prefers_application_id_over_service_name() -> None:
    row = otel_via_norm.sdk_span_to_clickhouse(
        {
            "name": "planner.agent",
            "context": {"trace_id": "tt", "span_id": "ss"},
            "attributes": {"application_id": "sample-app"},
            "resource": {"attributes": {"service.name": "mas-runtime"}},
            "start_time": "2024-01-01T00:00:00Z",
            "end_time": "2024-01-01T00:00:01Z",
        }
    )
    assert row["ServiceName"] == "sample-app"
    assert row["SpanAttributes"]["application_id"] == "sample-app"


def test_normalize_otel_imports_norm_when_installed() -> None:
    pytest.importorskip("norm")
    from mas.library.kg.observability.otel_via_norm import normalize_otel

    assert callable(normalize_otel)


_EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
_QA_SPANS = _EXAMPLES / "qa-agent" / "otel_spans.jsonl"
_TRIP_PLANNER_SPANS = _EXAMPLES / "trip-planner" / "otel_spans.jsonl"


def _load_spans(path: Path) -> list[dict]:
    import json

    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_norm_accepts_realistic_converter_output() -> None:
    """A real observe_sdk-profile export (mas-library-telemetry's replay,
    checked in here as a static fixture so this package never needs to
    depend on mas-library-telemetry to test its own OTel ingest path)
    reaches norm.normalize() without being silently dropped by its
    required-attribute dispatch.

    Uses whatever ``norm``/``oxp-ontology`` are actually installed, as
    published -- no patching.
    """
    pytest.importorskip("norm")
    if not _QA_SPANS.is_file():
        pytest.skip(f"missing example fixture {_QA_SPANS}")
    nodes, _edges = otel_via_norm.normalize_otel(_load_spans(_QA_SPANS))
    types = {n.get("node_type") for n in nodes}
    assert nodes, "norm.normalize produced no nodes -- converter output was silently skipped"
    assert types & {"AgentCall", "LLMCall", "ToolCall", "Session", "MASCall", "Agent", "LLM"}


def test_build_kg_connects_agent_states_through_handoff() -> None:
    """Inspect's Execution Timeline path-finds Agent initial->final through
    call-level transitions. That path only exists when hasLLMCall attaches
    and capability-chain-boundary bridges are not skipped -- exercised here
    against a real two-agent handoff trace (trip-planner), not a synthetic
    single-span fixture.
    """
    pytest.importorskip("norm")
    from norm.ioa_observe.build import build_kg

    if not _TRIP_PLANNER_SPANS.is_file():
        pytest.skip(f"missing example fixture {_TRIP_PLANNER_SPANS}")
    spans = _load_spans(_TRIP_PLANNER_SPANS)
    ch = sorted(
        otel_via_norm.to_clickhouse_spans(spans),
        key=lambda s: (s.get("Timestamp") or "", s.get("SpanId") or ""),
    )
    nodes, edges = build_kg(ch)

    def etype(edge):
        return str(edge.get("edge_type") or edge.get("@type") or "")

    def ntype(node):
        return str(node.get("node_type") or node.get("@type") or "")

    def src(edge):
        return edge.get("source_id") or edge.get("from_id")

    def dst(edge):
        return edge.get("target_id") or edge.get("to_id")

    assert sum(1 for e in edges if etype(e).endswith("hasLLMCall")) >= 1
    assert any(
        "capability-chain-boundary" in str(n.get("name") or "")
        for n in nodes
        if ntype(n) == "ProcessingCall"
    )

    input_to = [(src(e), dst(e)) for e in edges if etype(e).endswith("inputTo")]
    leads = [(src(e), dst(e)) for e in edges if etype(e).endswith("leadsTo")]
    repr_e = [(src(e), dst(e)) for e in edges if etype(e).endswith("representsExecution")]
    by_id = {n.get("id"): n for n in nodes}
    adj: dict[str, set[str]] = {}
    agent_pairs = []
    for node in nodes:
        if ntype(node) != "Transition":
            continue
        fr = next((a for a, b in input_to if b == node.get("id")), None)
        to = next((b for a, b in leads if a == node.get("id")), None)
        exe = next((b for a, b in repr_e if a == node.get("id")), None)
        if not fr or not to:
            continue
        kind = ntype(by_id.get(exe) or {})
        if kind in {"LLMCall", "ToolCall", "ProcessingCall"}:
            adj.setdefault(fr, set()).add(to)
        if kind == "AgentCall":
            agent_pairs.append((fr, to))
    assert agent_pairs
    for start, end in agent_pairs:
        seen = {start}
        queue = [start]
        while queue:
            cur = queue.pop(0)
            if cur == end:
                break
            for nxt in adj.get(cur, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        assert end in seen, (start, end, adj)
