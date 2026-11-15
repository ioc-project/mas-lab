#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Native → KG vs native → observe-sdk OTel → norm.normalize()."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE = _ROOT / "tests" / "fixtures" / "path_equivalence" / "events.jsonl"
# A real captured trace used as a stress test beyond the synthetic fixture
# above (see the regression this plan's research flagged from a prior debug
# session) — not committed to the repo; set to run this check locally.
_T02 = Path(os.environ.get("MAS_OTEL_T02_FIXTURE", "/tmp/mas-otel-baseline-t02/events.jsonl"))
# library-kg has no runtime dependency on the telemetry library (the emitter
# side; this is the consumer side) -- this test-only cross-package import
# needs its source on sys.path. Default is the sibling directory in this
# checkout; override via env var elsewhere.
_TELEMETRY_SRC = Path(
    os.environ.get(
        "MAS_LIBRARY_TELEMETRY_SRC",
        str(_ROOT.parent / "library-telemetry" / "src"),
    )
)


def _load_events(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]  # noqa: E501


def test_native_kg_complete_keeps_annotation_off_default() -> None:
    from mas.library.kg.observability.path_equivalence import _native_kg

    events = [
        {
            "kind": "agent_call_start",
            "timestamp": 1.0,
            "run_id": "r1",
            "call_id": "c1",
            "agent_id": "a",
        },
        {
            "kind": "context_assembled",
            "timestamp": 1.5,
            "run_id": "r1",
            "call_id": "c1",
            "agent_id": "a",
        },
        {
            "kind": "agent_call_end",
            "timestamp": 2.0,
            "run_id": "r1",
            "call_id": "c1",
            "agent_id": "a",
        },
    ]
    default_types = {n.get("node_type") for n in _native_kg(events, mode="default")[0]}
    complete_types = {n.get("node_type") for n in _native_kg(events, mode="complete")[0]}
    assert "CallAnnotation" not in default_types
    assert "CallAnnotation" in complete_types


def _import_compare():
    pytest.importorskip("norm")
    pytest.importorskip("opentelemetry.sdk")
    if _TELEMETRY_SRC.is_dir() and str(_TELEMETRY_SRC) not in sys.path:
        sys.path.insert(0, str(_TELEMETRY_SRC))
    pytest.importorskip("mas.library.telemetry")
    from mas.library.kg.observability.path_equivalence import compare_native_and_otel

    return compare_native_and_otel


@pytest.mark.parametrize("mode", ["default", "complete"])
def test_fixture_native_matches_otel_via_norm(mode: str) -> None:
    compare = _import_compare()
    report = compare(_load_events(_FIXTURE), mode=mode)
    assert report["equivalent"], report
    if mode == "complete":
        assert report["same_trace"], report


@pytest.mark.parametrize("mode", ["default", "complete"])
def test_t02_native_matches_otel_via_norm(mode: str) -> None:
    if not _T02.is_file():
        pytest.skip(f"T02 events not found at {_T02}")
    compare = _import_compare()
    report = compare(_load_events(_T02), mode=mode)
    assert report["equivalent"], report
    assert report["same_trace"], report


def _tool_use_events() -> list[dict]:
    """The fixture with its LLM call rewritten as a tool-use turn (request on start)."""
    events = _load_events(_FIXTURE)
    for event in events:
        if event["kind"] == "llm_call_start":
            event.update(
                model="openai/gpt-4",
                messages=[{"role": "user", "content": "find flights"}],
                tools=["search_flights"],
                max_tokens=512,
                temperature=0.25,
            )
        elif event["kind"] == "llm_call_end":
            event.pop("response", None)
            event.update(
                output="",
                finish_reason="tool_calls",
                tokens_used={"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
                tool_calls=[{"toolName": "search_flights", "arguments": {"to": "Verdantia"}}],
            )
    return events


def _llm_call_view(nodes: list[dict], edges: list[dict]) -> dict:
    by_id = {node["id"]: node for node in nodes}
    (call,) = [node for node in nodes if node.get("node_type") == "LLMCall"]
    final = [
        by_id[edge["to_id"]]
        for edge in edges
        if edge["from_id"] == call["id"]
        and edge["edge_type"] == "hasFinalState"
        and edge["to_id"] in by_id
    ]
    return {
        "modelName": call.get("modelName"),
        "temperature": float(call["temperature"]),
        "finishReason": call.get("finishReason"),
        "final_state": [state.get("content") for state in final],
    }


def test_tool_use_turn_reads_the_same_in_both_paths() -> None:
    """Request on start, tool calls on end: native KG and OTel→norm KG agree.

    The KG handler and the OTel converter each render ``tool_calls`` as
    completion text (the packages share no code). This is the contract that
    keeps the two renderings identical.
    """
    _import_compare()
    from mas.library.kg.observability.otel_via_norm import normalize_otel
    from mas.library.kg.observability.path_equivalence import _native_kg, _otel_kg

    events = _tool_use_events()
    native = _llm_call_view(*_native_kg(events, mode="default"))
    _, _, spans = _otel_kg(events, mode="default")
    otel = _llm_call_view(*normalize_otel(spans))

    assert native["final_state"] == ['→ search_flights({"to": "Verdantia"})']
    assert otel == native


@pytest.mark.parametrize("mode", ["default", "complete"])
def test_tool_use_turn_traces_are_the_same_trace_in_both_paths(mode: str) -> None:
    """Agents, calls and their containment agree, not only the per-type counts."""
    compare = _import_compare()
    report = compare(_tool_use_events(), mode=mode)
    assert report["equivalent"], report
    assert report["same_trace"], report


@pytest.mark.parametrize(
    ("name", "stripped"),
    [
        ("moderator call 1d91cb6a3f66e3d1", "moderator"),
        ("moderator call 0x1d91cb6a3f66e3d1", "moderator"),
        ("Tool call (get_fares)", "get_fares"),
        ("planner", "planner"),
    ],
)
def test_generated_call_suffix_is_stripped(name: str, stripped: str) -> None:
    from mas.library.kg.observability.path_equivalence import _strip_generated_suffix

    assert _strip_generated_suffix(name) == stripped


def _events_with_processing() -> list[dict]:
    """A tool-use run whose LLM call is preceded by a context-assembly step."""
    events = _tool_use_events()
    llm = next(e for e in events if e["kind"] == "llm_call_start")
    messages = [{"role": "user", "content": "find flights"}]
    base = {k: llm[k] for k in ("run_id", "session_id", "agent_id", "parent_call_id")}
    start = {
        **base,
        "kind": "processing_call_start",
        "call_id": "proc-1",
        "timestamp": float(llm["timestamp"]) - 0.05,
        "processing_name": "context assembly",
        "processing_type": "context_assembly",
        "messages": messages,
    }
    end = {**start, "kind": "processing_call_end", "timestamp": float(llm["timestamp"]) - 0.04}
    index = events.index(llm)
    return [*events[:index], start, end, *events[index:]]


def _trajectory_view(nodes: list[dict]) -> dict:
    count = lambda ntype: sum(1 for n in nodes if n.get("node_type") == ntype)  # noqa: E731
    return {
        "counts": {
            t: count(t) for t in ("State", "Transition", "ProcessingCall", "Processing", "LLMCall")
        },
        "state_content": sorted(
            str(n.get("content", "")) for n in nodes if n.get("node_type") == "State"
        ),
    }


def test_the_trajectory_is_the_same_whether_built_from_events_or_from_spans() -> None:
    """States, Transitions and ProcessingCalls come out alike, content included."""
    _import_compare()
    from mas.library.kg.observability.path_equivalence import _native_kg, _otel_kg

    events = _events_with_processing()
    native = _trajectory_view(_native_kg(events, mode="default")[0])
    otel = _trajectory_view(_otel_kg(events, mode="default")[0])
    assert native["counts"]["ProcessingCall"] >= 2  # the fixture's own step and the one added here
    assert otel == native


def test_spans_from_another_producer_keep_the_normalizers_own_trajectory() -> None:
    """No ``mas.*`` attributes, so nothing to rebuild from: the graph is ``norm``'s."""
    pytest.importorskip("norm")
    from norm.normalizer import normalize

    from mas.library.kg.observability.otel_via_norm import normalize_otel, to_clickhouse_spans

    example = _ROOT / "examples" / "trip-planner" / "otel_spans.jsonl"
    spans = _load_events(example)
    for span in spans:
        attrs = span.get("attributes") or {}
        span["attributes"] = {k: v for k, v in attrs.items() if not k.startswith("mas.")}
    stock_nodes, _ = normalize(to_clickhouse_spans(spans))
    nodes, _ = normalize_otel(spans)

    def count(graph: list, ntype: str) -> int:
        return sum(1 for n in graph if n.get("node_type") == ntype)

    for ntype in ("State", "Transition", "ProcessingCall"):
        assert count(nodes, ntype) == count(stock_nodes, ntype), ntype
