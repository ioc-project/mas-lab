# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Multilevel trajectory plotter: regressions found by running it over every shipped trace."""

from __future__ import annotations

import pytest

from mas.lab.plots.multilevel_trajectory import plot_multilevel_trajectory
from mas.lab.plots.multilevel_trajectory.records import _build_call_records


def _processing_pair(call_id: str, t: float) -> list[dict]:
    common = {
        "agent_id": "root",
        "call_id": call_id,
        "correlation_id": 1,
        "processing_name": "context assembly",
        "processing_type": "context_assembly",
    }
    return [
        {"kind": "processing_call_start", "timestamp": t, **common},
        {"kind": "processing_call_end", "timestamp": t + 0.001, **common},
    ]


def test_duplicate_open_call_id_error_names_both_events_and_cause() -> None:
    events = [
        {"kind": "execution_start", "timestamp": 0.0, "agent_id": "root", "call_id": "exec-1"},
        # Two start events reach the plotter before the first one's end
        # (timestamp order), exactly what a doubled context assembly produces.
        {"kind": "processing_call_start", "timestamp": 0.10, "agent_id": "root", "call_id": "pc-1"},
        {"kind": "processing_call_start", "timestamp": 0.11, "agent_id": "root", "call_id": "pc-1"},
        {"kind": "processing_call_end", "timestamp": 0.12, "agent_id": "root", "call_id": "pc-1"},
        {"kind": "execution_end", "timestamp": 1.0, "agent_id": "root", "call_id": "exec-1"},
    ]
    with pytest.raises(ValueError) as err:
        _build_call_records(events)
    msg = str(err.value)
    assert "Duplicate open call_id" in msg
    assert "event #1" in msg and "event #2" in msg
    assert "processing_call_start" in msg
    assert "ts=0.1" in msg and "ts=0.11" in msg
    assert "Likely cause" in msg


def test_orphan_end_error_names_the_event_index() -> None:
    events = [
        {"kind": "execution_start", "timestamp": 0.0, "agent_id": "root", "call_id": "exec-1"},
        {"kind": "tool_call_end", "timestamp": 0.5, "agent_id": "root", "call_id": "t-9", "tool_name": "x"},
        {"kind": "execution_end", "timestamp": 1.0, "agent_id": "root", "call_id": "exec-1"},
    ]
    with pytest.raises(ValueError, match=r"event #1.*Likely cause"):
        _build_call_records(events)


def test_distinct_processing_call_ids_for_one_llm_call_are_accepted() -> None:
    events = [
        {"kind": "execution_start", "timestamp": 0.0, "agent_id": "root", "call_id": "exec-1"},
        *_processing_pair("pc-1", 0.1),
        *_processing_pair("pc-2", 0.2),
        {"kind": "execution_end", "timestamp": 1.0, "agent_id": "root", "call_id": "exec-1"},
    ]
    assert plot_multilevel_trajectory(events, fmt="svg").startswith("<svg")


def test_processing_call_with_segment_count_does_not_crash() -> None:
    # Some emitters report ``segments`` as a count, not a list of parts.
    events = [
        {"kind": "mas_call_start", "timestamp": 0.0, "agent_id": "mas", "call_id": "mas-1"},
        {"kind": "execution_start", "timestamp": 0.1, "agent_id": "root", "call_id": "exec-1",
         "parent_call_id": "mas-1"},
        {"kind": "processing_call_start", "timestamp": 0.2, "agent_id": "root", "call_id": "pc-1",
         "parent_call_id": "exec-1", "processing_name": "context assembly",
         "processing_type": "context_assembly", "segments": 3, "tokens": 450},
        {"kind": "processing_call_end", "timestamp": 0.3, "agent_id": "root", "call_id": "pc-1",
         "parent_call_id": "exec-1"},
        {"kind": "execution_end", "timestamp": 0.9, "agent_id": "root", "call_id": "exec-1",
         "parent_call_id": "mas-1"},
        {"kind": "mas_call_end", "timestamp": 1.0, "agent_id": "mas", "call_id": "mas-1"},
    ]
    assert "segments" not in next(r for r in _build_call_records(events) if r["call_id"] == "pc-1")
    assert plot_multilevel_trajectory(events, fmt="html").startswith("<!--")


def test_overlapping_mas_invocations_never_produce_a_negative_hitl_gap() -> None:
    events = [
        {"kind": "mas_call_start", "timestamp": 1.0, "agent_id": "mas", "call_id": "mas-1"},
        {"kind": "mas_call_start", "timestamp": 2.0, "agent_id": "peer", "call_id": "mas-2"},
        {"kind": "mas_call_end", "timestamp": 3.0, "agent_id": "peer", "call_id": "mas-2"},
        {"kind": "mas_call_end", "timestamp": 5.0, "agent_id": "mas", "call_id": "mas-1"},
    ]
    from mas.lab.plots.multilevel_trajectory.dag import _build_dag
    from mas.lab.plots.multilevel_trajectory.models import TransNode

    _, lanes = _build_dag(_build_call_records(events), events, show_provenance=False, enabled_facets=None)
    mas_lane = next(lane for lane in lanes if lane.lane_id == "mas")
    for node in mas_lane.sequence:
        if isinstance(node, TransNode):
            assert node.end_ts >= node.start_ts, node.label
    assert not [n for n in mas_lane.sequence if isinstance(n, TransNode) and n.call_type == "HITL"]


def test_branch_separator_label_flips_before_the_right_edge() -> None:
    html = plot_multilevel_trajectory(
        [
            {"kind": "execution_start", "timestamp": 0.0, "agent_id": "root", "call_id": "e"},
            {"kind": "execution_end", "timestamp": 1.0, "agent_id": "root", "call_id": "e"},
        ],
        fmt="html",
    )
    assert "_brFlip" in html and "text-anchor" in html


def _shipped_traces() -> list:
    from pathlib import Path

    root = Path(__file__).resolve().parents[4]
    skip = ("pipeline_parity", "cache-backup", "cache-entry", "node_modules", ".venv")
    found = []
    for base in ("docs", "library-samples", "library-kg/examples", "library-telemetry/examples", "tests/fixtures"):
        for path in (root / base).rglob("events.jsonl"):
            if not any(part in skip for part in path.parts):
                found.append(path)
    return sorted(found)


@pytest.mark.parametrize("path", _shipped_traces(), ids=lambda p: p.parent.name)
def test_every_shipped_trace_renders(path) -> None:
    assert plot_multilevel_trajectory(path, fmt="svg").startswith("<svg")


def _calls_lane_transitions(events: list[dict]):
    from mas.lab.plots.multilevel_trajectory.dag import _build_dag
    from mas.lab.plots.multilevel_trajectory.models import TransNode

    _, lanes = _build_dag(_build_call_records(events), events, show_provenance=False, enabled_facets=None)
    lane = next(lane for lane in lanes if lane.lane_id == "calls")
    return [n for n in lane.sequence if isinstance(n, TransNode) and not n.connector_only]


def test_tool_call_recorded_under_an_llm_call_is_laid_out_after_it() -> None:
    def pair(kind: str, cid: str, t0: float, t1: float, parent: str, **extra) -> list[dict]:
        base = {"agent_id": "root", "call_id": cid, "parent_call_id": parent, **extra}
        return [
            {"kind": f"{kind}_start", "timestamp": t0, **base},
            {"kind": f"{kind}_end", "timestamp": t1, **base},
        ]

    events = [
        {"kind": "execution_start", "timestamp": 0.0, "agent_id": "root", "call_id": "exec"},
        *pair("llm_call", "llm-1", 1.0, 2.0, "exec"),
        # Recorded as a child of the LLM call, but it runs after the LLM ended.
        *pair("tool_call", "tool-1", 3.0, 4.0, "llm-1", tool_name="lookup"),
        *pair("llm_call", "llm-2", 5.0, 6.0, "exec"),
        {"kind": "execution_end", "timestamp": 7.0, "agent_id": "root", "call_id": "exec"},
    ]
    nodes = _calls_lane_transitions(events)
    spans = [(n.dfs_pos_start, n.dfs_pos_end) for n in nodes]
    assert [n.call_type for n in nodes] == ["LLMCall", "ToolCall", "LLMCall"]
    for (_, prev_end), (nxt_start, _) in zip(spans, spans[1:]):
        assert nxt_start >= prev_end - 1e-9, spans
    assert nodes[1].start_ts >= nodes[0].end_ts


def _orphan_llm_events(second_agent_run: bool) -> list[dict]:
    events = [
        {"kind": "execution_start", "timestamp": 0.0, "agent_id": "root", "call_id": "exec-1"},
        {"kind": "llm_call_start", "timestamp": 0.1, "agent_id": "root", "call_id": "llm-1"},
        {"kind": "llm_call_end", "timestamp": 0.5, "agent_id": "root", "call_id": "llm-1"},
        {"kind": "execution_end", "timestamp": 1.0, "agent_id": "root", "call_id": "exec-1"},
    ]
    if second_agent_run:
        events += [
            {"kind": "execution_start", "timestamp": 0.0, "agent_id": "root", "call_id": "exec-2"},
            {"kind": "execution_end", "timestamp": 1.0, "agent_id": "root", "call_id": "exec-2"},
        ]
    return events


def test_llm_call_without_parent_is_attached_to_its_only_agent_execution() -> None:
    nodes = _calls_lane_transitions(_orphan_llm_events(second_agent_run=False))
    assert [n.call_type for n in nodes] == ["LLMCall"]


def test_parentless_call_with_ambiguous_owner_is_not_guessed() -> None:
    llm = next(r for r in _build_call_records(_orphan_llm_events(second_agent_run=True))
               if r["call_type"] == "LLMCall")
    assert not llm.get("parent_call_id")


def test_sub_millisecond_empty_gap_before_first_call_is_a_connector_column() -> None:
    # The recorded replay run has a 0.4 ms step between the session start and
    # the first call that no transition spans.
    from pathlib import Path

    from mas.lab.plots.multilevel_trajectory.chart_data import build_trajectory_chart_data

    trace = Path(__file__).resolve().parents[4] / "tests/fixtures/golden-runs/extensions/events.jsonl"
    data = build_trajectory_chart_data(str(trace))
    assert data["bucketGaps"][0]["kind"] == "connector", data["bucketGaps"]


def test_clipped_bar_labels_are_marked_with_an_ellipsis() -> None:
    html = plot_multilevel_trajectory(
        [
            {"kind": "execution_start", "timestamp": 0.0, "agent_id": "root", "call_id": "e"},
            {"kind": "execution_end", "timestamp": 1.0, "agent_id": "root", "call_id": "e"},
        ],
        fmt="html",
    )
    assert "function _fitLabel" in html
    assert "tr.label.slice(0,maxC)" not in html
