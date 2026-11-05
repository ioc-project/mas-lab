#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Fork detection and the DFS virtual-position axis — pure-function unit tests.

These exercise tree.py's _detect_delegation_forks / _reset_branch_agent_call_ids /
_make_call_sequence / _assign_dfs_positions directly on hand-built record
lists, independent of _build_dag/_build_call_records, mirroring the
sibling-batch delegation scenario confirmed against a live trace: a
moderator dispatches 3 delegate_to_schedule_agent calls in one LLM turn (all
sharing nearly — but never exactly — the same dispatch instant, confirmed
from a real trace: 1784124930.5783172 / .578378 / .578434), but only the
first sibling's subtree is explored — and takes real wall-clock time —
before the second sibling's own marker is appended to the DFS-ordered call
sequence.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.lab.plots.multilevel_trajectory.chart_data import _build_chart_data, build_trajectory_chart_data
from mas.lab.plots.multilevel_trajectory.dag import _build_dag
from mas.lab.plots.multilevel_trajectory.models import TransNode
from mas.lab.plots.multilevel_trajectory.records import _build_call_records
from mas.lab.plots.multilevel_trajectory.tree import (
    _assign_dfs_positions,
    _detect_delegation_forks,
    _make_agent_sequence,
    _make_call_sequence,
    _reset_branch_agent_call_ids,
)
from mas.lab.plots.trajectory import load_trace

REPO_ROOT = Path(__file__).resolve().parents[4]
# Canonical copy of the sandbox trace used by these regression tests.
TRIP_PLANNER_EVENTS_FIXTURE = REPO_ROOT / "tests/fixtures/multilevel/trip-planner-plain-demo/events.r1.jsonl"

# Sibling dispatch instants: real traces never produce exact float ties (see
# module docstring) — microsecond-distinct, in dispatch order.
_D1, _D2, _D3 = 5.0001, 5.0002, 5.0003


def _rec(call_id, parent_call_id, level, call_type, start_ts, end_ts, agent_id=""):
    return {
        "call_id": call_id,
        "parent_call_id": parent_call_id,
        "level": level,
        "call_type": call_type,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "agent_id": agent_id,
    }


def _parallel_group_rec(call_id, parent_call_id, group_id, tool_call_ids, ts):
    """A native ``ProcessingCall(processing_type="parallel_group")`` marker —
    the runtime's own record of a real ``PARALLEL_TOOL_CALLS`` dispatch (see
    ``_native_parallel_group_by_tool_call_id`` in tree.py). Its ``tools``
    list's ``call_id`` entries are the only non-heuristic ground truth
    ``_detect_delegation_forks`` uses to decide 2+ delegate calls are one
    real fork, as opposed to merely sharing a delegating parent."""
    return {
        "call_id": call_id,
        "parent_call_id": parent_call_id,
        "level": "call",
        "call_type": "ProcessingCall",
        "start_ts": ts,
        "end_ts": ts,
        "agent_id": "",
        "processing_type": "parallel_group",
        "group_id": group_id,
        "tools": [{"call_id": cid} for cid in tool_call_ids],
    }


def _sibling_batch_records():
    """moderator delegates to schedule_agent 3 times in one batch (all
    dispatched within a fraction of a millisecond of each other), but only
    sibling #1's subtree is explored (5.0001 -> 10.0) before sibling #2's own
    marker (still at its original ~5.0002 dispatch instant) is appended."""
    return [
        _rec("mod", None, "agent", "AgentCall", 0.0, 20.0, "moderator"),
        _rec("deleg1", "mod", "call", "ToolCall", _D1, _D1),
        _rec("sched1", "deleg1", "agent", "AgentCall", _D1, 10.0, "schedule_agent"),
        _rec("deleg2", "mod", "call", "ToolCall", _D2, _D2),
        _rec("sched2", "deleg2", "agent", "AgentCall", _D2, 12.0, "schedule_agent"),
        _rec("deleg3", "mod", "call", "ToolCall", _D3, _D3),
        _rec("sched3", "deleg3", "agent", "AgentCall", _D3, 14.0, "schedule_agent"),
    ]


def test_detect_delegation_forks_groups_by_real_fork_parent_id():
    records = _sibling_batch_records()
    parent_of = {r["call_id"]: r["parent_call_id"] for r in records}
    rec_by_id = {r["call_id"]: r for r in records}

    groups = _detect_delegation_forks(records, parent_of, rec_by_id)

    assert set(groups) == {"mod"}
    assert [m["call_id"] for m in groups["mod"]] == ["sched1", "sched2", "sched3"]


def test_detect_delegation_forks_omits_single_delegate():
    records = [
        _rec("mod", None, "agent", "AgentCall", 0.0, 10.0, "moderator"),
        _rec("deleg1", "mod", "call", "ToolCall", 5.0, 5.0),
        _rec("itin", "deleg1", "agent", "AgentCall", 5.0, 8.0, "itinerary_agent"),
    ]
    parent_of = {r["call_id"]: r["parent_call_id"] for r in records}
    rec_by_id = {r["call_id"]: r for r in records}

    assert _detect_delegation_forks(records, parent_of, rec_by_id) == {}


def test_native_visual_fork_only_badges_genuinely_concurrent_delegates():
    """Regression: a solo delegation that merely shares a delegating parent
    with a later, unrelated, genuine 2-way fork must never be visually
    badged as part of an N-way parallel fork (e.g. rendered with
    ``parallelSize=3``) just because ``_detect_delegation_forks``'s own
    tree-structural grouping (correctly used for DFS resets) lumps all 3
    delegate calls together. Only the sub-cluster the runtime itself
    genuinely dispatched together in ONE native ``parallel_group`` event
    (see ``_native_parallel_group_by_tool_call_id``) gets the is_fork/
    is_join/parallel_group_id/parallel_size badge in the Agents lane."""
    records = [
        _rec("mod", None, "agent", "AgentCall", 0.0, 20.0, "moderator"),
        _rec("deleg-itin", "mod", "call", "ToolCall", 1.0, 1.0),
        _rec("itin", "deleg-itin", "agent", "AgentCall", 1.0, 4.0, "itinerary_agent"),
        _rec("deleg-sched", "mod", "call", "ToolCall", 9.0, 9.0),
        _rec("sched", "deleg-sched", "agent", "AgentCall", 9.0, 12.0, "schedule_agent"),
        _rec("deleg-conc", "mod", "call", "ToolCall", 9.0, 9.0),
        _rec("conc", "deleg-conc", "agent", "AgentCall", 9.0, 13.0, "concierge_agent"),
        _parallel_group_rec("pg1", "mod", "pg-2way", ["deleg-sched", "deleg-conc"], 9.0),
    ]

    # The 3 delegates still form ONE structural fork group (all share "mod")
    # — needed for DFS resets — even though only 2 of them are a genuine
    # native fork.
    parent_of = {r["call_id"]: r["parent_call_id"] for r in records}
    rec_by_id = {r["call_id"]: r for r in records}
    fork_groups = _detect_delegation_forks(records, parent_of, rec_by_id)
    assert set(fork_groups) == {"mod"}
    assert len(fork_groups["mod"]) == 3

    _, lanes = _build_dag(records, [])
    agents_lane = next(lane for lane in lanes if lane.lane_id == "agents")
    fork_els = [el for el in agents_lane.sequence if getattr(el, "is_fork", False)]
    join_els = [el for el in agents_lane.sequence if getattr(el, "is_join", False)]

    assert len(fork_els) == 1 and len(join_els) == 1
    assert fork_els[0].parallel_size == 2
    assert join_els[0].parallel_size == 2
    assert fork_els[0].parallel_group_id == join_els[0].parallel_group_id

    # The solo itinerary delegation must never be part of any fork/join
    # badge at all.
    assert not any(
        getattr(el, "is_fork", False) or getattr(el, "is_join", False)
        for el in agents_lane.sequence
        if getattr(el, "ts", None) in (1.0, 4.0)
    )


def test_make_call_sequence_empty_branch_fallback_uses_processing_call_type():
    """A fork branch whose subtree makes zero real calls (cut off before its
    first LLM/Tool call — sched2/sched3 in _sibling_batch_records() have no
    call-level children at all) still needs a _branch_entry-tagged
    placeholder so the reset separator has somewhere to land. That
    placeholder must never carry call_type="AgentCall": this list backs the
    Calls lane, which only ever holds LLM/Tool/Memory/RAG/Processing
    records — an AgentCall here would render as a wrong-type, agent-colored
    "Agent" bar (dag.py's trans() has no override for it, and constants.py
    maps AgentCall to the agent palette/label and excludes it from ever
    being instant)."""
    records = _sibling_batch_records()
    parent_of = {r["call_id"]: r["parent_call_id"] for r in records}
    rec_by_id = {r["call_id"]: r for r in records}
    children_of = _children_of(records)
    fork_groups = _detect_delegation_forks(records, parent_of, rec_by_id)
    reset_ids = _reset_branch_agent_call_ids(fork_groups)
    agent_sequence = _make_agent_sequence(records, children_of, parent_of)

    call_sequence = _make_call_sequence(agent_sequence, children_of, reset_ids)

    branch_entries = {r["call_id"]: r for r in call_sequence if r.get("_branch_entry")}
    assert set(branch_entries) == {"sched2", "sched3"}
    for rec in branch_entries.values():
        assert rec["call_type"] == "ProcessingCall", rec


def test_make_call_sequence_reused_call_id_respects_next_turn_boundary():
    """Two back-to-back invocations of a repeatedly-delegated agent share one
    runtime call_id pool (children_of[shared_id]) — the first invocation
    keeps that id as its own call_id, the second gets a records.py-suffixed
    id but still queries the same pool via _reused_call_id. Rule 2 gives
    the two turns an exact zero-gap boundary, but _collect's _TS_TOL (50ms)
    padding must never let turn 1 reach across that boundary and steal a
    call that starts just after it (llm2, at +30ms) but structurally
    belongs to turn 2 — regression for exactly this: a live trip-planner
    trace where turn 1's own lookup silently claimed turn 2's real LLM
    call, leaving turn 2 looking like an empty invocation."""
    shared = "sched-u1-exec"
    records = [
        _rec("llm1", shared, "call", "LLMCall", 1.0, 1.9, "sched"),
        _rec("llm2", shared, "call", "LLMCall", 2.03, 2.9, "sched"),
    ]
    children_of = _children_of(records)
    agent_sequence = [
        {"call_id": shared, "start_ts": 1.0, "end_ts": 2.0, "agent_id": "sched"},
        {"call_id": "sched-2", "start_ts": 2.0, "end_ts": 5.0, "agent_id": "sched", "_reused_call_id": shared},
    ]

    call_sequence = _make_call_sequence(agent_sequence, children_of, frozenset())

    by_id = {r["call_id"]: r for r in call_sequence}
    assert set(by_id) == {"llm1", "llm2"}
    assert by_id["llm2"]["start_ts"] == 2.03, by_id["llm2"]


def test_reset_branch_agent_call_ids_excludes_rank_zero():
    records = _sibling_batch_records()
    parent_of = {r["call_id"]: r["parent_call_id"] for r in records}
    rec_by_id = {r["call_id"]: r for r in records}
    fork_groups = _detect_delegation_forks(records, parent_of, rec_by_id)

    assert _reset_branch_agent_call_ids(fork_groups) == {"sched2", "sched3"}


def _children_of(records):
    children = {}
    for r in records:
        pid = r.get("parent_call_id")
        if pid is not None:
            children.setdefault(pid, []).append(r)
    for kids in children.values():
        kids.sort(key=lambda r: r["start_ts"])
    return children


def test_make_call_sequence_tags_branch_entry_and_widens_marker_gap():
    """The delegating marker for a later sibling must stay strictly separate
    (in real ts) from that sibling's own first call, so the branch-reset
    boundary has somewhere to land — regression for the marker appearing to
    belong to the new branch instead of closing out the delegating agent's own."""
    records = _sibling_batch_records() + [
        _rec("sched1-ctx", "sched1", "call", "ProcessingCall", _D1, _D1),
        _rec("sched2-ctx", "sched2", "call", "ProcessingCall", _D2, _D2),
        _rec("sched3-ctx", "sched3", "call", "ProcessingCall", _D3, _D3),
    ]
    parent_of = {r["call_id"]: r["parent_call_id"] for r in records}
    rec_by_id = {r["call_id"]: r for r in records}
    children_of = _children_of(records)
    fork_groups = _detect_delegation_forks(records, parent_of, rec_by_id)
    reset_ids = _reset_branch_agent_call_ids(fork_groups)
    agent_sequence = _make_agent_sequence(records, children_of, parent_of)

    call_sequence = _make_call_sequence(agent_sequence, children_of, reset_ids)

    by_id = {r["call_id"]: r for r in call_sequence}
    _deleg1, deleg2, deleg3 = by_id["deleg1"], by_id["deleg2"], by_id["deleg3"]
    ctx1, ctx2, ctx3 = by_id["sched1-ctx"], by_id["sched2-ctx"], by_id["sched3-ctx"]

    # All 3 markers dispatch within the same tolerance window (a genuine
    # sibling batch — see _collect's window check), so they land consecutively
    # in call order before any of their subtrees are visited: deleg1, deleg2,
    # deleg3, THEN sched1's own subtree starts. sched2/sched3's own subtrees
    # are only reached later still, in later agent_sequence iterations.
    assert [r["call_id"] for r in call_sequence[:4]] == ["deleg1", "deleg2", "deleg3", "sched1-ctx"]

    # Rank-0 branch (sched1) needs no reset tag — it's the fork's first
    # branch — but its own subtree still can't start before ALL 3 markers
    # have finished being registered.
    assert "_branch_entry" not in ctx1
    assert ctx1["start_ts"] == deleg3["end_ts"]

    # Rank>=1 branches (sched2, sched3) get tagged and land strictly after
    # the marker cluster AND after sched1's own subtree entry — a genuinely
    # separate timestamp, not one that coincides with any marker's own close.
    assert ctx2.get("_branch_entry") == "sched2"
    assert ctx2["start_ts"] > deleg2["end_ts"]
    assert ctx3.get("_branch_entry") == "sched3"
    assert ctx3["start_ts"] > ctx2["start_ts"]

    # No record ever has end_ts < start_ts (a real, non-inverted interval).
    for rec in call_sequence:
        assert rec["end_ts"] >= rec["start_ts"], rec


def test_assign_dfs_positions_monotonic_across_sibling_batch():
    """The core regression: real start_ts is NOT monotonic in DFS append
    order (sibling #2/#3's markers carry their own early dispatch ts,
    appearing in the flat list AFTER sibling #1's subtree already advanced
    real time well past it), but dfs_pos must still increase monotonically."""
    call_sequence = [
        _rec("deleg1", "mod", "call", "ToolCall", _D1, _D1 + 0.001),
        _rec("sched1-llm", "sched1", "call", "LLMCall", _D1 + 0.001, 10.0),
        _rec("deleg2", "mod", "call", "ToolCall", _D2, _D2 + 0.001),
        {**_rec("sched2-llm", "sched2", "call", "LLMCall", _D2 + 0.002, 12.0), "_branch_entry": "sched2"},
        _rec("deleg3", "mod", "call", "ToolCall", _D3, _D3 + 0.001),
        {**_rec("sched3-llm", "sched3", "call", "LLMCall", _D3 + 0.002, 14.0), "_branch_entry": "sched3"},
    ]
    # Sanity: the fixture must actually reproduce a backward jump in real
    # time — sibling #2's marker sits, in DFS order, right after sibling
    # #1's subtree already advanced real time to 10.0.
    assert call_sequence[2]["start_ts"] < call_sequence[1]["end_ts"]
    assert call_sequence[4]["start_ts"] < call_sequence[3]["end_ts"]

    ts_to_pos, reset_branch_id = _assign_dfs_positions(call_sequence)

    seen = []
    for r in call_sequence:
        seen.append(ts_to_pos[r["start_ts"]])
        seen.append(ts_to_pos[r["end_ts"]])
    assert seen == sorted(seen), seen
    assert reset_branch_id == {
        _D2 + 0.002: "sched2",
        _D3 + 0.002: "sched3",
    }


def test_assign_dfs_positions_marker_stays_before_its_own_reset():
    """The delegating marker's own end must land BEFORE the reset it
    triggers — not at/after it — so the marker renders on the parent's side
    of the branch separator, not the child's."""
    call_sequence = [
        _rec("deleg1", "mod", "call", "ToolCall", _D1, _D1 + 0.001),
        _rec("sched1-llm", "sched1", "call", "LLMCall", _D1 + 0.001, 10.0),
        _rec("deleg2", "mod", "call", "ToolCall", _D2, _D2 + 0.001),
        {**_rec("sched2-llm", "sched2", "call", "LLMCall", _D2 + 0.002, 12.0), "_branch_entry": "sched2"},
    ]
    ts_to_pos, reset_branch_id = _assign_dfs_positions(call_sequence)

    marker_end_pos = ts_to_pos[_D2 + 0.001]
    branch_entry_pos = ts_to_pos[_D2 + 0.002]
    assert marker_end_pos < branch_entry_pos
    assert (_D2 + 0.001) not in reset_branch_id
    assert reset_branch_id[_D2 + 0.002] == "sched2"


def test_context_assembly_processing_call_hides_raw_prompt_output():
    assert TRIP_PLANNER_EVENTS_FIXTURE.is_file(), f"missing fixture: {TRIP_PLANNER_EVENTS_FIXTURE}"
    events = load_trace(TRIP_PLANNER_EVENTS_FIXTURE)
    records = _build_call_records(events)

    _, lanes = _build_dag(records, events)

    call_lane = next(lane for lane in lanes if lane.lane_id == "calls")
    state_nodes = [el for el in call_lane.sequence if not isinstance(el, TransNode)]
    # S1 carries exactly one real CPR part — the user's own prompt, present
    # since session start — never the system prompt (that only exists from
    # context assembly onward).
    assert [p["source"] for p in state_nodes[0].cpr_data] == ["assembled/user"]
    assert "You are a moderator" not in state_nodes[0].hover

    trans_nodes = [
        el for lane in lanes for el in lane.sequence if isinstance(el, TransNode) and el.call_type == "ProcessingCall"
    ]
    assert trans_nodes, "expected at least one context assembly ProcessingCall"
    assert trans_nodes[0].hover_out == ""
    assert state_nodes[1].hover == ""
    # Native-only contract: no synthetic state chunks are injected.
    synthetic_sources = {"llm/output", "state/assistant", "state/tool_input"}
    assert not any(
        isinstance(p, dict) and p.get("source") in synthetic_sources for st in state_nodes for p in (st.cpr_data or [])
    )


def test_wait_resume_states_expose_internal_wait_fields():
    records = [
        {
            "call_id": "agent-root",
            "parent_call_id": None,
            "level": "agent",
            "call_type": "AgentCall",
            "start_ts": 0.0,
            "end_ts": 4.0,
            "agent_id": "moderator",
        },
        {
            "call_id": "deleg-tool",
            "parent_call_id": "agent-root",
            "level": "call",
            "call_type": "ToolCall",
            "start_ts": 1.0,
            "end_ts": 1.2,
            "agent_id": "moderator",
            "tool_name": "delegate_to_worker",
            "input": "delegate",
            "output": "ok",
        },
        {
            "call_id": "wait-1",
            "parent_call_id": "agent-root",
            "level": "call",
            "call_type": "ProcessingCall",
            "start_ts": 1.21,
            "end_ts": 1.22,
            "agent_id": "moderator",
            "processing_type": "wait_state",
            "wait_link_id": "delegate-1",
            "wait_role": "WAIT",
            "wait_scope": "delegation",
            "wait_note": "waiting for delegated reply",
        },
        {
            "call_id": "resume-1",
            "parent_call_id": "agent-root",
            "level": "call",
            "call_type": "ProcessingCall",
            "start_ts": 2.0,
            "end_ts": 2.01,
            "agent_id": "moderator",
            "processing_type": "wait_state",
            "wait_link_id": "delegate-1",
            "wait_role": "RESUME",
            "wait_scope": "delegation",
            "wait_note": "delegated reply received",
        },
    ]

    _, lanes = _build_dag(records, events=[])
    call_lane = next(lane for lane in lanes if lane.lane_id == "calls")
    states = [el for el in call_lane.sequence if not isinstance(el, TransNode)]

    wait_states = [st for st in states if st.wait_role == "WAIT"]
    resume_states = [st for st in states if st.wait_role == "RESUME"]
    assert wait_states, "expected at least one WAIT state"
    assert resume_states, "expected at least one RESUME state"

    w = wait_states[0]
    r = resume_states[0]
    assert w.wait_link_id == "delegate-1"
    assert r.wait_link_id == "delegate-1"
    assert w.label_override.startswith("W")
    assert r.label_override.startswith("R")
    assert w.suppress_cpr is True
    assert r.suppress_cpr is True
    assert w.cpr_data == []
    assert r.cpr_data == []
    assert w.wait_meta.get("blocking", {}).get("toolType") == "DELEGATION"
    assert r.wait_meta.get("blocking", {}).get("toolType") == "DELEGATION"
    assert (w.wait_meta.get("peerLabel") or "").startswith("R")
    assert (r.wait_meta.get("peerLabel") or "").startswith("W")
    assert abs(float(w.wait_meta.get("peerTs") or 0.0) - float(r.ts)) <= 1e-9
    assert abs(float(r.wait_meta.get("peerTs") or 0.0) - float(w.ts)) <= 1e-9


def test_native_cpr_does_not_inject_synthetic_chunks():
    assert TRIP_PLANNER_EVENTS_FIXTURE.is_file(), f"missing fixture: {TRIP_PLANNER_EVENTS_FIXTURE}"
    events = load_trace(TRIP_PLANNER_EVENTS_FIXTURE)
    records = _build_call_records(events)

    _, lanes = _build_dag(records, events)
    call_lane = next(lane for lane in lanes if lane.lane_id == "calls")
    states = [el for el in call_lane.sequence if not isinstance(el, TransNode)]
    llm_trans = [el for el in call_lane.sequence if isinstance(el, TransNode) and el.call_type == "LLMCall"]
    assert llm_trans, "expected at least one LLM transition"

    synthetic_sources = {"llm/output", "state/assistant", "state/tool_input"}
    assert not any(
        isinstance(p, dict) and p.get("source") in synthetic_sources for tr in llm_trans for p in (tr.cpr_data or [])
    )
    assert not any(
        isinstance(p, dict) and p.get("source") in synthetic_sources for st in states for p in (st.cpr_data or [])
    )


def test_llm_call_own_output_becomes_third_context_chunk_on_next_state():
    """An LLMCall is a transition: its own real output (a tool-call decision
    or a final reply) is a genuine new fact that must be visible as the
    state right after it — carried forward from whatever the state right
    before it already had (cumulative), plus exactly one new part. This must
    hold even when no *later* native ``context_part_contributed`` cycle ever
    re-observes this same call's own decision (e.g. a moderator that
    delegates once and is done — see ``_collect_llm_tool_decisions``'s
    docstring for why native telemetry alone never covers this case).

    Regression guard: a state directly following an LLMCall transition must
    never come back with FEWER cpr parts than the state directly preceding
    it — that would mean the LLM's own turn was silently dropped from
    context instead of carried forward.
    """
    assert TRIP_PLANNER_EVENTS_FIXTURE.is_file(), f"missing fixture: {TRIP_PLANNER_EVENTS_FIXTURE}"
    events = load_trace(TRIP_PLANNER_EVENTS_FIXTURE)
    records = _build_call_records(events)

    _, lanes = _build_dag(records, events)
    call_lane = next(lane for lane in lanes if lane.lane_id == "calls")
    seq = call_lane.sequence

    checked_any = False
    for i, el in enumerate(seq):
        if not (isinstance(el, TransNode) and el.call_type == "LLMCall"):
            continue
        prev_state = seq[i - 1] if i > 0 else None
        next_state = seq[i + 1] if i + 1 < len(seq) else None
        if prev_state is None or next_state is None:
            continue
        # WAIT/RESUME control boundaries deliberately suppress cpr_data (they
        # are control nodes, not content states — see dag.py's wait-tagging
        # loop) even when they happen to coincide with an LLM call's own end
        # ts; that is a separate, intentional mechanism, not a regression.
        if getattr(next_state, "suppress_cpr", False):
            continue
        prev_parts = prev_state.cpr_data or []
        next_parts = next_state.cpr_data or []
        # Cumulative: never regresses to fewer facts than before the call.
        assert len(next_parts) >= len(prev_parts), (
            f"LLM transition at seq idx {i} lost context: {len(prev_parts)} parts before, {len(next_parts)} after"
        )
        # The LLM's own real output must be present as the newly-added part
        # (there is always a real decision/output in this trace's LLM calls).
        added = [p for p in next_parts if p not in prev_parts]
        assert added, f"LLM transition at seq idx {i} added no new context part"
        assert any(p.get("source") == "assembled/tool_call" for p in added), (
            f"LLM transition at seq idx {i}: expected an 'assembled/tool_call' "
            f"part among the newly-added ones, got sources="
            f"{[p.get('source') for p in added]}"
        )
        checked_any = True

    assert checked_any, "expected at least one LLM transition with real before/after states"


def test_chart_data_serializes_wait_internal_fields():
    records = [
        {
            "call_id": "agent-root",
            "parent_call_id": None,
            "level": "agent",
            "call_type": "AgentCall",
            "start_ts": 0.0,
            "end_ts": 4.0,
            "agent_id": "moderator",
        },
        {
            "call_id": "deleg-tool",
            "parent_call_id": "agent-root",
            "level": "call",
            "call_type": "ToolCall",
            "start_ts": 1.0,
            "end_ts": 1.2,
            "agent_id": "moderator",
            "tool_name": "delegate_to_worker",
            "input": "delegate",
            "output": "ok",
        },
        {
            "call_id": "wait-1",
            "parent_call_id": "agent-root",
            "level": "call",
            "call_type": "ProcessingCall",
            "start_ts": 1.21,
            "end_ts": 1.22,
            "agent_id": "moderator",
            "processing_type": "wait_state",
            "wait_link_id": "delegate-1",
            "wait_role": "WAIT",
            "wait_scope": "delegation",
            "wait_note": "waiting",
        },
        {
            "call_id": "resume-1",
            "parent_call_id": "agent-root",
            "level": "call",
            "call_type": "ProcessingCall",
            "start_ts": 2.0,
            "end_ts": 2.01,
            "agent_id": "moderator",
            "processing_type": "wait_state",
            "wait_link_id": "delegate-1",
            "wait_role": "RESUME",
            "wait_scope": "delegation",
            "wait_note": "resumed",
        },
        {
            "call_id": "llm-1",
            "parent_call_id": "agent-root",
            "level": "call",
            "call_type": "LLMCall",
            "start_ts": 2.2,
            "end_ts": 3.0,
            "agent_id": "moderator",
            "input": "prompt",
            "output": "assistant answer",
            "model": "gpt-test",
        },
    ]

    state_reg, lanes = _build_dag(records, events=[])
    data = _build_chart_data(
        state_reg,
        lanes,
        title="internal",
        width_mode="log",
        records=records,
    )

    call_lane = next(lane for lane in data.get("lanes", []) if lane.get("laneId") == "calls")

    wait_states = [
        s for s in call_lane.get("sequence", []) if s.get("type") == "state" and s.get("waitLinkId") == "delegate-1"
    ]
    assert wait_states, "expected serialized wait states"
    assert any(s.get("waitRole") == "WAIT" for s in wait_states)
    assert any(s.get("waitRole") == "RESUME" for s in wait_states)
    assert all(s.get("waitMeta") for s in wait_states)
    assert any(s.get("waitMeta", {}).get("blocking", {}).get("toolType") == "DELEGATION" for s in wait_states)


def test_w1_content_has_wait_target_and_link_to_resume_state():
    records = [
        {
            "call_id": "agent-root",
            "parent_call_id": None,
            "level": "agent",
            "call_type": "AgentCall",
            "start_ts": 0.0,
            "end_ts": 4.0,
            "agent_id": "moderator",
        },
        {
            "call_id": "deleg-tool",
            "parent_call_id": "agent-root",
            "level": "call",
            "call_type": "ToolCall",
            "start_ts": 1.0,
            "end_ts": 1.2,
            "agent_id": "moderator",
            "tool_name": "delegate_to_worker",
            "input": "delegate",
            "output": "ok",
        },
        {
            "call_id": "wait-1",
            "parent_call_id": "agent-root",
            "level": "call",
            "call_type": "ProcessingCall",
            "start_ts": 1.21,
            "end_ts": 1.22,
            "agent_id": "moderator",
            "processing_type": "wait_state",
            "wait_link_id": "delegate-1",
            "wait_role": "WAIT",
            "wait_scope": "delegation",
            "wait_note": "waiting",
        },
        {
            "call_id": "resume-1",
            "parent_call_id": "agent-root",
            "level": "call",
            "call_type": "ProcessingCall",
            "start_ts": 2.0,
            "end_ts": 2.01,
            "agent_id": "moderator",
            "processing_type": "wait_state",
            "wait_link_id": "delegate-1",
            "wait_role": "RESUME",
            "wait_scope": "delegation",
            "wait_note": "resumed",
        },
        {
            "call_id": "worker-agent",
            "parent_call_id": "deleg-tool",
            "level": "agent",
            "call_type": "AgentCall",
            "start_ts": 1.2,
            "end_ts": 2.0,
            "agent_id": "worker",
        },
        {
            "call_id": "worker-llm",
            "parent_call_id": "worker-agent",
            "level": "call",
            "call_type": "LLMCall",
            "start_ts": 1.3,
            "end_ts": 1.9,
            "agent_id": "worker",
            "input": "do work",
            "output": "done",
            "model": "gpt-test",
        },
    ]

    state_reg, lanes = _build_dag(records, events=[])
    data = _build_chart_data(
        state_reg,
        lanes,
        title="internal",
        width_mode="log",
        records=records,
    )

    call_lane = next(lane for lane in data.get("lanes", []) if lane.get("laneId") == "calls")
    agents_lane = next(lane for lane in data.get("lanes", []) if lane.get("laneId") == "agents")
    wait = next(s for s in call_lane.get("sequence", []) if s.get("type") == "state" and s.get("labelOverride") == "W1")
    resume = next(
        s for s in call_lane.get("sequence", []) if s.get("type") == "state" and s.get("labelOverride") == "R1"
    )

    wm = wait.get("waitMeta") or {}
    assert not (wait.get("hoverByLane") or {}).get("calls")
    assert (wm.get("blocking") or {}).get("toolType") == "DELEGATION"
    assert (wm.get("peerLabel") or "") == "R1"
    assert abs(float(wm.get("peerTs") or 0.0) - float(resume.get("ts") or 0.0)) <= 1e-9
    assert "cprData" not in wait

    wait_agent = next(
        s for s in agents_lane.get("sequence", []) if s.get("type") == "state" and s.get("labelOverride") == "W1"
    )
    resume_agent = next(
        s for s in agents_lane.get("sequence", []) if s.get("type") == "state" and s.get("labelOverride") == "R1"
    )
    wm_agent = wait_agent.get("waitMeta") or {}
    assert not (wait_agent.get("hoverByLane") or {}).get("agents")
    assert (wm_agent.get("peerLabel") or "") == "R1"
    assert abs(float(wm_agent.get("peerTs") or 0.0) - float(resume_agent.get("ts") or 0.0)) <= 1e-9
    assert "cprData" not in wait_agent


def test_wait_w1_followed_by_next_numbered_state_on_calls_lane():
    records = [
        {
            "call_id": "agent-root",
            "parent_call_id": None,
            "level": "agent",
            "call_type": "AgentCall",
            "start_ts": 0.0,
            "end_ts": 4.0,
            "agent_id": "moderator",
        },
        {
            "call_id": "deleg-tool",
            "parent_call_id": "agent-root",
            "level": "call",
            "call_type": "ToolCall",
            "start_ts": 1.0,
            "end_ts": 1.2,
            "agent_id": "moderator",
            "tool_name": "delegate_to_worker",
            "input": "delegate",
            "output": "ok",
        },
        {
            "call_id": "wait-1",
            "parent_call_id": "agent-root",
            "level": "call",
            "call_type": "ProcessingCall",
            "start_ts": 1.21,
            "end_ts": 1.22,
            "agent_id": "moderator",
            "processing_type": "wait_state",
            "wait_link_id": "delegate-1",
            "wait_role": "WAIT",
            "wait_scope": "delegation",
            "wait_note": "waiting",
        },
        {
            "call_id": "resume-1",
            "parent_call_id": "agent-root",
            "level": "call",
            "call_type": "ProcessingCall",
            "start_ts": 2.0,
            "end_ts": 2.01,
            "agent_id": "moderator",
            "processing_type": "wait_state",
            "wait_link_id": "delegate-1",
            "wait_role": "RESUME",
            "wait_scope": "delegation",
            "wait_note": "resumed",
        },
        {
            "call_id": "worker-agent",
            "parent_call_id": "deleg-tool",
            "level": "agent",
            "call_type": "AgentCall",
            "start_ts": 1.2,
            "end_ts": 2.0,
            "agent_id": "worker",
        },
        {
            "call_id": "worker-llm",
            "parent_call_id": "worker-agent",
            "level": "call",
            "call_type": "LLMCall",
            "start_ts": 1.3,
            "end_ts": 1.9,
            "agent_id": "worker",
            "input": "do work",
            "output": "done",
            "model": "gpt-test",
        },
    ]

    state_reg, lanes = _build_dag(records, events=[])
    chart = _build_chart_data(
        state_reg,
        lanes,
        title="internal",
        width_mode="log",
        records=records,
    )

    lane_by_id = {lane.get("laneId"): lane for lane in chart.get("lanes", [])}
    assert "calls" in lane_by_id and "agents" in lane_by_id

    calls_states = [s for s in lane_by_id["calls"].get("sequence", []) if s.get("type") == "state"]
    i_w1 = next(i for i, s in enumerate(calls_states) if s.get("labelOverride") == "W1")
    j = i_w1 + 1
    while j < len(calls_states) and calls_states[j].get("labelOverride"):
        j += 1
    assert j < len(calls_states), "expected numbered state after W1 on calls lane"
    nxt = calls_states[j]
    assert nxt.get("num") == calls_states[i_w1].get("num", 0) + 1

    agents_states = [s for s in lane_by_id["agents"].get("sequence", []) if s.get("type") == "state"]
    i_w1_agent = next(i for i, s in enumerate(agents_states) if s.get("labelOverride") == "W1")
    j_agent = i_w1_agent + 1
    while j_agent < len(agents_states) and agents_states[j_agent].get("labelOverride"):
        j_agent += 1
    assert j_agent < len(agents_states), "expected numbered state after W1 on agents lane"
    nxt_agent = agents_states[j_agent]
    assert nxt_agent.get("num") == agents_states[i_w1_agent].get("num", 0) + 1

    # W states keep neutral metadata (no hardcoded prose labels).
    w1_call = calls_states[i_w1]
    assert not (w1_call.get("hoverByLane") or {}).get("calls")
    w1_agent = next(s for s in agents_states if s.get("labelOverride") == "W1")
    assert not (w1_agent.get("hoverByLane") or {}).get("agents")


def test_calls_lane_avoids_spurious_connector_only_gaps():
    records = [
        {
            "call_id": "agent-root",
            "parent_call_id": None,
            "level": "agent",
            "call_type": "AgentCall",
            "start_ts": 0.0,
            "end_ts": 4.0,
            "agent_id": "moderator",
        },
        {
            "call_id": "deleg-tool",
            "parent_call_id": "agent-root",
            "level": "call",
            "call_type": "ToolCall",
            "start_ts": 1.0,
            "end_ts": 1.2,
            "agent_id": "moderator",
            "tool_name": "delegate_to_worker",
            "input": "delegate",
            "output": "ok",
        },
        {
            "call_id": "worker-agent",
            "parent_call_id": "deleg-tool",
            "level": "agent",
            "call_type": "AgentCall",
            "start_ts": 1.2,
            "end_ts": 2.0,
            "agent_id": "worker",
        },
        {
            "call_id": "worker-llm",
            "parent_call_id": "worker-agent",
            "level": "call",
            "call_type": "LLMCall",
            "start_ts": 1.3,
            "end_ts": 1.9,
            "agent_id": "worker",
            "input": "do work",
            "output": "done",
            "model": "gpt-test",
        },
    ]

    state_reg, lanes = _build_dag(records, events=[])
    chart = _build_chart_data(
        state_reg,
        lanes,
        title="internal",
        width_mode="log",
        records=records,
    )

    call_lane = next(lane for lane in chart.get("lanes", []) if lane.get("laneId") == "calls")
    connector_only_states = [
        s for s in call_lane.get("sequence", []) if s.get("type") == "state" and s.get("connectorOnly")
    ]
    assert connector_only_states == []


def test_calls_lane_sequence_alternates_without_double_state_gap():
    records = [
        {
            "call_id": "agent-root",
            "parent_call_id": None,
            "level": "agent",
            "call_type": "AgentCall",
            "start_ts": 0.0,
            "end_ts": 6.0,
            "agent_id": "moderator",
        },
        {
            "call_id": "c-ctx",
            "parent_call_id": "agent-root",
            "level": "call",
            "call_type": "ProcessingCall",
            "processing_type": "memory",
            "start_ts": 0.5,
            "end_ts": 0.6,
            "agent_id": "moderator",
            "input": "load memory",
            "output": "ok",
        },
        {
            "call_id": "c-llm",
            "parent_call_id": "agent-root",
            "level": "call",
            "call_type": "LLMCall",
            "start_ts": 0.7,
            "end_ts": 1.5,
            "agent_id": "moderator",
            "input": "prompt",
            "output": "result",
            "model": "gpt-test",
        },
        {
            "call_id": "c-tool",
            "parent_call_id": "agent-root",
            "level": "call",
            "call_type": "ToolCall",
            "start_ts": 1.6,
            "end_ts": 2.0,
            "agent_id": "moderator",
            "input": '{"q":1}',
            "output": '{"ok":true}',
            "tool_name": "demo_tool",
        },
    ]

    state_reg, lanes = _build_dag(records, events=[])
    chart = _build_chart_data(
        state_reg,
        lanes,
        title="internal",
        width_mode="log",
        records=records,
    )
    call_lane = next(lane for lane in chart.get("lanes", []) if lane.get("laneId") == "calls")
    seq = call_lane.get("sequence", [])

    for i in range(1, len(seq)):
        assert not (seq[i - 1].get("type") == "state" and seq[i].get("type") == "state"), (
            "calls lane must not contain consecutive states (visual gap without transition)"
        )


def test_wait_s4_not_misplaced_relative_to_w1_in_shared_buckets():
    records = [
        {
            "call_id": "agent-root",
            "parent_call_id": None,
            "level": "agent",
            "call_type": "AgentCall",
            "start_ts": 0.0,
            "end_ts": 4.0,
            "agent_id": "moderator",
        },
        {
            "call_id": "deleg-tool",
            "parent_call_id": "agent-root",
            "level": "call",
            "call_type": "ToolCall",
            "start_ts": 1.0,
            "end_ts": 1.2,
            "agent_id": "moderator",
            "tool_name": "delegate_to_worker",
        },
        {
            "call_id": "wait-1",
            "parent_call_id": "agent-root",
            "level": "call",
            "call_type": "ProcessingCall",
            "start_ts": 1.21,
            "end_ts": 1.22,
            "agent_id": "moderator",
            "processing_type": "wait_state",
            "wait_link_id": "delegate-1",
            "wait_role": "WAIT",
            "wait_scope": "delegation",
            "wait_note": "waiting",
        },
    ]

    state_reg, lanes = _build_dag(records, events=[])
    chart = _build_chart_data(
        state_reg,
        lanes,
        title="internal",
        width_mode="log",
        records=records,
    )

    lane_by_id = {lane.get("laneId"): lane for lane in chart.get("lanes", [])}
    calls_states = [s for s in lane_by_id["calls"].get("sequence", []) if s.get("type") == "state"]
    i_w1 = next(i for i, s in enumerate(calls_states) if s.get("labelOverride") == "W1")
    j = i_w1 + 1
    while j < len(calls_states) and calls_states[j].get("labelOverride"):
        j += 1
    assert j < len(calls_states), "expected a numbered state after W1"
    s4 = calls_states[j]
    w1 = calls_states[i_w1]
    assert s4.get("num") == w1.get("num", 0) + 1
    assert float(s4.get("ts")) > float(w1.get("ts")), "S4 timestamp must be after W1"

    buckets = chart.get("buckets", [])
    i_w1_bucket = next(i for i, ts in enumerate(buckets) if abs(float(ts) - float(w1.get("ts"))) <= 1e-9)
    i_s4_bucket = next(i for i, ts in enumerate(buckets) if abs(float(ts) - float(s4.get("ts"))) <= 1e-9)
    assert i_s4_bucket > i_w1_bucket, "S4 bucket must come after W1 bucket"


def test_chart_data_serializes_cpr_internal_fields_from_real_trace():
    assert TRIP_PLANNER_EVENTS_FIXTURE.is_file(), f"missing fixture: {TRIP_PLANNER_EVENTS_FIXTURE}"
    events = load_trace(TRIP_PLANNER_EVENTS_FIXTURE)
    chart = build_trajectory_chart_data(events, width_mode="log")

    call_lane = next(lane for lane in chart.get("lanes", []) if lane.get("laneId") == "calls")
    llm_trans = [
        t for t in call_lane.get("sequence", []) if t.get("type") == "trans" and t.get("callType") == "LLMCall"
    ]
    assert llm_trans, "expected serialized llm transitions"
    synthetic_sources = {"llm/output", "state/assistant", "state/tool_input"}
    assert not any(
        isinstance(p, dict) and p.get("source") in synthetic_sources
        for t in llm_trans
        for p in (t.get("cprData") or [])
    )


def test_chart_data_serializes_missing_telemetry_flags_on_transition():
    records = [
        {
            "call_id": "agent-root",
            "parent_call_id": None,
            "level": "agent",
            "call_type": "AgentCall",
            "start_ts": 0.0,
            "end_ts": 2.0,
            "agent_id": "moderator",
        },
        {
            "call_id": "proc-1",
            "parent_call_id": "agent-root",
            "level": "call",
            "call_type": "ProcessingCall",
            "start_ts": 0.5,
            "end_ts": 0.6,
            "agent_id": "moderator",
            "processing_type": "memory",
            "input": "memory",
            "_end_missing": True,
        },
    ]

    state_reg, lanes = _build_dag(records, events=[])
    chart = _build_chart_data(
        state_reg,
        lanes,
        title="internal",
        width_mode="log",
        records=records,
    )
    call_lane = next(lane for lane in chart.get("lanes", []) if lane.get("laneId") == "calls")
    proc = next(t for t in call_lane.get("sequence", []) if t.get("type") == "trans" and t.get("callId") == "proc-1")
    assert set(proc.get("missingTelemetry") or []) == {"input", "end_event"}


def test_assign_dfs_positions_preserves_relative_durations_within_branch():
    """No fork involved: dfs_pos deltas must equal real time deltas, so
    relative call durations still look proportional within a single branch."""
    call_sequence = [
        _rec("c1", "a", "call", "LLMCall", 0.0, 3.0),
        _rec("c2", "a", "call", "ToolCall", 3.0, 4.0),
        _rec("c3", "a", "call", "LLMCall", 4.0, 10.0),
    ]
    ts_to_pos, reset_branch_id = _assign_dfs_positions(call_sequence)

    assert reset_branch_id == {}
    assert ts_to_pos[3.0] - ts_to_pos[0.0] == 3.0
    assert ts_to_pos[4.0] - ts_to_pos[3.0] == 1.0
    assert ts_to_pos[10.0] - ts_to_pos[4.0] == 6.0


def test_stagger_coinc_processing_calls_cascades_forward_bump_to_touching_sibling():
    """Regression for a spurious-gap bug: staggering a group of >=2 coincident
    point-in-time ProcessingCalls snaps the SINGLE next record forward to
    clear the group, but must also cascade that same delta to any FURTHER
    record whose start_ts was exactly touching that record's own (pre-shift)
    end_ts — the "no-gap" sibling boundary _align_record_boundaries already
    established. Leaving the further record un-shifted reopens exactly the
    gap (or a timestamp inversion) staggering was meant to avoid, one hop
    later in the chain."""
    from mas.lab.plots.multilevel_trajectory.annotations import (
        _STAGGER_DUR,
        _stagger_coinc_processing_calls,
    )

    # Two coincident point-in-time ProcessingCalls at ts=1.0 (a "wait" and a
    # "context assembly", as seen in a real trace) — group size 2 triggers
    # staggering. The LLMCall right after them originally starts exactly at
    # ts=1.0 (Rule-2 touching boundary) and the ToolCall right after THAT
    # originally starts exactly at the LLMCall's own end (another Rule-2
    # touching boundary) — this second boundary is the one that must survive
    # the cascade.
    seq = [
        _rec("pc1", "a", "call", "ProcessingCall", 1.0, 1.0),
        _rec("pc2", "a", "call", "ProcessingCall", 1.0, 1.0),
        _rec("llm", "a", "call", "LLMCall", 1.0, 5.0),
        _rec("tool", "a", "call", "ToolCall", 5.0, 6.0),
    ]

    result = _stagger_coinc_processing_calls(seq)
    by_id = {r["call_id"]: r for r in result}

    group_end = 1.0 + 2 * _STAGGER_DUR
    llm = by_id["llm"]
    tool = by_id["tool"]

    assert llm["start_ts"] == group_end
    # The LLM's own real duration (4.0s) must be preserved, not corrupted.
    assert llm["end_ts"] - llm["start_ts"] == 5.0 - 1.0
    # The ToolCall must still touch the LLM's (shifted) end exactly — no
    # gap and no inversion reintroduced one hop down the chain.
    assert tool["start_ts"] == llm["end_ts"]
    assert tool["end_ts"] - tool["start_ts"] == 6.0 - 5.0


# ── Popup content coverage (mirrors multilevel.html's JS popup logic) ───────
#
# The D3 renderer (assets/multilevel.html) computes every state/transition
# popup's CPR facets in pure JS with no JS test harness of its own. The
# functions below are a deliberate, minimal Python port of that exact logic
# (_callsLaneStateIdxAtOrBeforeNum / _computeStatePopupCpr's cross-lane
# fallback / _computeTransitionCprDiff's cross-lane fallback) so the
# data-level contract they depend on — every state carries a "num" (DFS
# bucket position, not raw ts) and the calls lane carries the real cprData —
# is exercised as an automated regression test against a real trace. If you
# change the cross-lane fallback logic in multilevel.html, update this port
# to match.


def _js_canonical_source(src):
    s = str(src or "").lower()
    if s == "assembled/user":
        return "context/user"
    if s == "state/assistant":
        return "llm/output"
    return s


def _js_part_key(p):
    return (_js_canonical_source(p.get("source")), p.get("content") or "")


def _js_calls_lane_idx_at_or_before_num(calls_seq, target_num):
    best, best_num = -1, float("-inf")
    for i, el in enumerate(calls_seq):
        if el.get("type") == "state" and el.get("num", 0) <= target_num and el.get("num", 0) > best_num:
            best, best_num = i, el.get("num", 0)
    return best


def _js_collect_parts(seq, until_idx):
    """Mirrors collectParts() inside _computeStatePopupCpr in multilevel.html.

    Accumulation is scoped PER AGENT (a bucket per agentId), not a single
    flat map wiped on every agent switch — a delegator's own facts must
    survive a delegation round-trip (moderator -> itinerary_agent ->
    moderator resumes) instead of vanishing the moment it delegates, while a
    delegate still never inherits its delegator's facts (its own bucket
    starts empty the first time that agentId is seen)."""
    by_agent: dict = {}
    current_agent = ""
    for i in range(until_idx + 1):
        el = seq[i]
        agent_id = el.get("agentId") or ""
        if agent_id and agent_id != current_agent:
            current_agent = agent_id
        bucket = by_agent.setdefault(current_agent or "__root__", {})
        if el.get("type") == "state":
            for p in el.get("cprData") or []:
                bucket[_js_part_key(p)] = p
        if el.get("type") == "trans":
            for p in el.get("cprData") or []:
                src = str(p.get("source") or "").lower()
                if src.startswith("context/") or src == "assembled/user":
                    bucket[_js_part_key(p)] = p
    return list(by_agent.get(current_agent or "__root__", {}).values())


def _js_state_popup_cpr(lanes_by_id, lane, state_idx):
    """Mirrors _computeStatePopupCpr's cross-lane fallback."""
    seq = lane.get("sequence") or []
    if lane.get("laneId") != "calls":
        st = seq[state_idx]
        calls_lane = lanes_by_id.get("calls")
        if not calls_lane:
            return []
        idx = _js_calls_lane_idx_at_or_before_num(calls_lane.get("sequence") or [], st.get("num", 0))
        return _js_state_popup_cpr(lanes_by_id, calls_lane, idx) if idx >= 0 else []
    return _js_collect_parts(seq, state_idx)


def _js_transition_cpr_diff(lanes_by_id, lane, trans_idx):
    """Mirrors _computeTransitionCprDiff's cross-lane fallback."""
    seq = lane.get("sequence") or []
    if lane.get("laneId") != "calls":
        prev_st, next_st = seq[trans_idx - 1], seq[trans_idx + 1]
        before_num = prev_st.get("num", 0) if prev_st.get("type") == "state" else float("-inf")
        after_num = next_st.get("num", 0) if next_st.get("type") == "state" else before_num
        calls_lane = lanes_by_id.get("calls")
        if not calls_lane:
            return []
        calls_seq = calls_lane.get("sequence") or []
        before_idx = _js_calls_lane_idx_at_or_before_num(calls_seq, before_num)
        after_idx = _js_calls_lane_idx_at_or_before_num(calls_seq, after_num)
        prev_parts = _js_state_popup_cpr(lanes_by_id, calls_lane, before_idx) if before_idx >= 0 else []
        next_parts = _js_state_popup_cpr(lanes_by_id, calls_lane, after_idx) if after_idx >= 0 else []
    else:
        prev_el, next_el = seq[trans_idx - 1], seq[trans_idx + 1]
        prev_parts = _js_state_popup_cpr(lanes_by_id, lane, trans_idx - 1) if prev_el.get("type") == "state" else []
        next_parts = _js_state_popup_cpr(lanes_by_id, lane, trans_idx + 1) if next_el.get("type") == "state" else []
    prev_keys = {_js_part_key(p) for p in prev_parts}
    return [p for p in next_parts if _js_part_key(p) not in prev_keys]


_TRIP_PLANNER_TRACE = TRIP_PLANNER_EVENTS_FIXTURE


def test_every_state_on_every_lane_yields_non_empty_cpr_popup():
    """ "All states show a CPR view" — including WAIT/RESUME markers on the
    agents/mas/session lanes, whose own cpr_data is intentionally cleared by
    dag.py (see test_wait_resume_states_expose_internal_wait_fields) but
    whose popup must fall back to the calls lane's own ground-truth view via
    DFS position (num), never raw ts (see chart_data.py's dfs_pos notes)."""
    if not _TRIP_PLANNER_TRACE.is_file():
        pytest.skip(f"real-trace fixture not available: {_TRIP_PLANNER_TRACE}")

    chart = build_trajectory_chart_data(_TRIP_PLANNER_TRACE, title="phase6")
    lanes_by_id = {lane["laneId"]: lane for lane in chart["lanes"]}

    empty = []
    for lane_id, lane in lanes_by_id.items():
        seq = lane.get("sequence") or []
        for i, el in enumerate(seq):
            if el.get("type") != "state":
                continue
            if el.get("connectorOnly"):
                continue
            parts = _js_state_popup_cpr(lanes_by_id, lane, i)
            if not parts:
                empty.append((lane_id, i, el.get("labelOverride") or el.get("ts")))
    assert not empty, f"states with an empty CPR popup: {empty}"


def test_every_transition_on_every_lane_yields_diff_or_has_a_fallback():
    """ "All transitions show diff" — Session, MASCall, AgentCall, ToolCall,
    LLMCall, ProcessingCall alike. When the cross-lane diff genuinely comes
    back empty, the transition must still carry hoverIn/hoverOut/label so
    the JS-side _buildGenericTransitionParts fallback (multilevel.html) has
    something to render instead of a bare "(no content)"."""
    if not _TRIP_PLANNER_TRACE.is_file():
        pytest.skip(f"real-trace fixture not available: {_TRIP_PLANNER_TRACE}")

    chart = build_trajectory_chart_data(_TRIP_PLANNER_TRACE, title="phase6")
    lanes_by_id = {lane["laneId"]: lane for lane in chart["lanes"]}

    stuck = []
    for lane_id, lane in lanes_by_id.items():
        seq = lane.get("sequence") or []
        for i, el in enumerate(seq):
            if el.get("type") != "trans":
                continue
            if el.get("callType") == "BranchLink" or el.get("connectorOnly") or el.get("isInstant"):
                continue
            diff = _js_transition_cpr_diff(lanes_by_id, lane, i)
            has_fallback = bool(
                (el.get("hoverIn") or "").strip()
                or (el.get("hoverOut") or "").strip()
                or (el.get("label") or "").strip()
            )
            if not diff and not has_fallback:
                stuck.append((lane_id, i, el.get("callType"), el.get("label")))
    assert not stuck, f"transitions with neither a diff nor a fallback: {stuck}"


def test_fork_join_states_carry_bidirectional_branch_navigation():
    """Fork/join state boundaries (dag.py's native is_fork/is_join facts) must
    carry the renderer's navigation fields: a fork state's forkBranchTs lists
    every branch's own real entry ts (one per parallelSize, in fork order,
    including its own — rank 0's entry is itself), and the matching join
    state's joinForkTs points back to that same fork state's own ts. Every
    listed ts must resolve to a real state on the SAME lane (the renderer's
    scrollToState/pinStateTip navigation depends on this)."""
    if not _TRIP_PLANNER_TRACE.is_file():
        pytest.skip(f"real-trace fixture not available: {_TRIP_PLANNER_TRACE}")

    chart = build_trajectory_chart_data(_TRIP_PLANNER_TRACE, title="phase7")
    checked_lanes = []

    for lane in chart["lanes"]:
        seq = lane["sequence"]
        state_ts = {el["ts"] for el in seq if el.get("type") == "state"}
        forks = [el for el in seq if el.get("type") == "state" and el.get("isFork")]
        joins = [el for el in seq if el.get("type") == "state" and el.get("isJoin")]
        if not forks and not joins:
            continue

        checked_lanes.append(lane["laneId"])
        assert forks and joins, f"lane {lane['laneId']} has unmatched fork/join presence"
        assert len(forks) == len(joins), (
            f"lane {lane['laneId']} has unequal fork/join counts: {len(forks)} forks, {len(joins)} joins"
        )

        fork_by_group = {el["parallelGroupId"]: el for el in forks}
        for join in joins:
            gid = join["parallelGroupId"]
            fork = fork_by_group.get(gid)
            assert fork is not None, f"lane {lane['laneId']}: join {gid} has no matching fork state"
            assert join.get("joinForkTs") == fork["ts"]
            branch_ts = fork.get("forkBranchTs") or []
            assert len(branch_ts) == fork["parallelSize"] == join["parallelSize"]
            assert len(set(branch_ts)) == len(branch_ts), f"duplicate branch entry ts in {gid}: {branch_ts}"
            for ts in branch_ts:
                assert ts in state_ts, (
                    f"lane {lane['laneId']}: forkBranchTs entry {ts} for {gid} is not a real state on that lane"
                )

        parallel_spans = lane.get("parallelSpans") or []
        if lane["laneId"] == "agents":
            assert parallel_spans, "expected agents lane parallelSpans to be populated"
        for span in parallel_spans:
            assert span["startTs"] < span["endTs"]
            assert span["size"] >= 2

    assert checked_lanes, "expected at least one lane with fork/join pairs in this trace"


def test_calls_lane_fork_and_join_are_cross_linked_bidirectionally():
    """Every fork/join pair must be a genuine graph edge in BOTH directions,
    not just join -> fork: the fork state's own joinTs must equal its
    matching join state's own ts, and the join state's own joinForkTs must
    equal its matching fork state's own ts. This is what lets the renderer
    (multilevel.html) draw one direct fork<->merge connector line instead of
    relying solely on the pre-existing branch fan-out lines. Exercised on
    the calls lane, which is the only lane carrying a genuine native
    same-agent tool fork/join pair in this trace (the agents lane has none —
    see test_fork_join_states_carry_bidirectional_branch_navigation)."""
    if not _TRIP_PLANNER_TRACE.is_file():
        pytest.skip(f"real-trace fixture not available: {_TRIP_PLANNER_TRACE}")

    chart = build_trajectory_chart_data(_TRIP_PLANNER_TRACE, title="fork-join-cross-link")
    calls_lane = next(lane for lane in chart["lanes"] if lane["laneId"] == "calls")
    seq = calls_lane["sequence"]

    forks = [el for el in seq if el.get("type") == "state" and el.get("isFork")]
    joins = [el for el in seq if el.get("type") == "state" and el.get("isJoin")]
    assert forks and joins, "expected at least one native tool fork/join pair on the calls lane"
    assert len(forks) == len(joins), f"unequal fork/join counts: {len(forks)} forks, {len(joins)} joins"

    fork_by_group = {el["parallelGroupId"]: el for el in forks}
    join_by_group = {el["parallelGroupId"]: el for el in joins}
    assert set(fork_by_group) == set(join_by_group), "every fork must have exactly one matching join, and vice versa"

    for gid, fork in fork_by_group.items():
        join = join_by_group[gid]
        assert fork.get("joinTs") is not None, f"fork {gid} is missing its forward joinTs cross-link"
        assert fork["joinTs"] == join["ts"], f"fork {gid}'s joinTs does not point at its own join state's ts"
        assert join.get("joinForkTs") == fork["ts"], (
            f"join {gid}'s joinForkTs does not point back at its own fork state's ts"
        )


def test_calls_lane_branch_begin_end_are_paired_within_each_group():
    """Fork/branch-begin define the START of a group/branch; join/branch-end
    define its END. tree.py's `_make_call_sequence` splits a native
    parallel_group into a zero-duration fork marker, then every branch
    member (rank 0 included), then a zero-duration join marker (see its
    docstring) — rank 0's own real entry is therefore a genuinely different
    state from the fork marker itself, so EVERY rank (0 and up) gets its own
    explicit isBranchBegin state: `explicit_begins == explicit_ends ==
    parallelSize`. This asserts that pairing invariant, and that every
    branch-begin/branch-end state now carries the same parallelGroupId as
    its fork/join siblings (the renderer's shape/labelling and the group
    cross-linking both rely on this)."""
    if not _TRIP_PLANNER_TRACE.is_file():
        pytest.skip(f"real-trace fixture not available: {_TRIP_PLANNER_TRACE}")

    chart = build_trajectory_chart_data(_TRIP_PLANNER_TRACE, title="branch-begin-end-pairing")
    calls_lane = next(lane for lane in chart["lanes"] if lane["laneId"] == "calls")
    seq = calls_lane["sequence"]

    forks = [el for el in seq if el.get("type") == "state" and el.get("isFork")]
    begins = [el for el in seq if el.get("type") == "state" and el.get("isBranchBegin")]
    ends = [el for el in seq if el.get("type") == "state" and el.get("isBranchEnd")]
    assert forks, "expected at least one native tool fork on the calls lane"
    assert begins and ends, "expected at least one branch-begin/branch-end pair on the calls lane"

    for el in begins + ends:
        assert el.get("parallelGroupId"), f"branch begin/end state {el.get('num')} is missing its parallelGroupId"

    for fork in forks:
        gid = fork["parallelGroupId"]
        size = fork["parallelSize"]
        group_begins = [el for el in begins if el["parallelGroupId"] == gid]
        group_ends = [el for el in ends if el["parallelGroupId"] == gid]
        assert len(group_begins) == size, (
            f"group {gid}: expected {size} explicit branch-begin states "
            f"(every rank, including rank 0, gets its own), found {len(group_begins)}"
        )
        assert len(group_ends) == size, f"group {gid}: expected {size} branch-end states, found {len(group_ends)}"


def test_calls_lane_branch_begin_end_are_cross_linked_to_each_other_and_to_fork_join():
    """Each individual branch's own begin/end pair must be a genuine graph
    edge in both directions (branchPartnerTs), keyed by branch identity —
    never re-derived from ts-proximity, since concurrent branches can
    legitimately finish out of dispatch order. This is what lets the
    renderer's branch-begin/branch-end popups link "branch start -> branch
    end" and "branch end -> branch start", on top of the existing
    fork -> merge and merge -> fork links, so a fork/merge group and its
    individual branches form one fully cross-linked graph: fork <-> merge,
    fork -> every branch entry, branch-begin <-> branch-end, and (via the
    shared parallelGroupId) branch-begin/branch-end <-> their own fork/merge."""
    if not _TRIP_PLANNER_TRACE.is_file():
        pytest.skip(f"real-trace fixture not available: {_TRIP_PLANNER_TRACE}")

    chart = build_trajectory_chart_data(_TRIP_PLANNER_TRACE, title="branch-cross-link")
    calls_lane = next(lane for lane in chart["lanes"] if lane["laneId"] == "calls")
    seq = calls_lane["sequence"]

    forks = [el for el in seq if el.get("type") == "state" and el.get("isFork")]
    joins = [el for el in seq if el.get("type") == "state" and el.get("isJoin")]
    begins = [el for el in seq if el.get("type") == "state" and el.get("isBranchBegin")]
    ends = [el for el in seq if el.get("type") == "state" and el.get("isBranchEnd")]
    by_ts = {el["ts"]: el for el in seq if el.get("type") == "state"}
    assert begins and ends, "expected at least one branch-begin/branch-end pair on the calls lane"

    for begin in begins:
        partner_ts = begin.get("branchPartnerTs")
        assert partner_ts is not None, f"branch-begin {begin.get('num')} is missing branchPartnerTs"
        partner = by_ts.get(partner_ts)
        assert partner is not None and partner.get("isBranchEnd"), (
            f"branch-begin {begin.get('num')}'s branchPartnerTs does not point at a branch-end state"
        )
        assert partner.get("branchPartnerTs") == begin["ts"], (
            f"branch-end {partner.get('num')}'s own branchPartnerTs does not point back at branch-begin "
            f"{begin.get('num')}"
        )

    # Every branch-end whose own group's fork is on this same lane must
    # also be reachable back to that fork's own matching join/merge, via
    # the shared parallelGroupId — completing the fork<->merge<->branch
    # cross-linked graph.
    join_by_group = {el["parallelGroupId"]: el for el in joins}
    fork_by_group = {el["parallelGroupId"]: el for el in forks}
    for end in ends:
        gid = end["parallelGroupId"]
        assert gid in join_by_group, f"branch-end {end.get('num')}'s group {gid} has no matching merge state"
        assert gid in fork_by_group, f"branch-end {end.get('num')}'s group {gid} has no matching fork state"
