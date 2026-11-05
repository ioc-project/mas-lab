#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Multilevel trajectory plot — governance fields reach the serialized chart JSON.

The Phase 2/3 governance visualization (badges, ghost markers, retry chains)
has no JS test runner in this repo, so its rendering itself is not exercised
by pytest — verified manually in a browser during development instead. This
file closes the coverage gap one level down: that the Python-side chart_data
output the JS consumes actually carries the fields it needs (governance,
governanceColor, retryGroupId, retryAttempt), for a realistic end-to-end
events.jsonl trace.

Note: there used to be a separate, toggleable "Governance lane" consolidating
this same data into its own row — removed as redundant, since the exact same
decisions/blocked-actions already render as per-call badges and ghost markers
on the Calls lane (no extra layer needed on top of the existing per-call
display).
"""

from __future__ import annotations

from mas.lab.plots.multilevel_trajectory.chart_data import build_trajectory_chart_data

_EVENTS = [
    {"kind": "mas_call_start", "timestamp": 0.0, "agent_id": "moderator", "call_id": "mas-1"},
    {
        "kind": "execution_start",
        "timestamp": 0.0,
        "agent_id": "moderator",
        "call_id": "exec-1",
        "input": "Plan a trip to Shadowmere",
        "context": {"is_entry_agent": True},
    },
    {
        "kind": "llm_call_start",
        "timestamp": 0.1,
        "agent_id": "moderator",
        "call_id": "llm-1",
        "correlation_id": 1,
        "model": "gpt",
        "messages": [{"role": "user", "content": "hi"}],
    },
    {
        "kind": "llm_call_end",
        "timestamp": 0.5,
        "agent_id": "moderator",
        "call_id": "llm-1",
        "correlation_id": 1,
        "response": {"content": "Let me check the route"},
    },
    {
        "kind": "governance_decision",
        "timestamp": 0.6,
        "agent_id": "moderator",
        "correlation_id": 2,
        "hook": "egress",
        "checkpoint": "after",
        "decision": "BLOCK",
        "reason": "Restricted destination: Shadowmere",
        "policy_name": "forbidden-destination",
    },
    {
        "kind": "tool_call_start",
        "timestamp": 0.7,
        "agent_id": "moderator",
        "call_id": "tool-1",
        "correlation_id": 3,
        "tool_name": "lookup_route",
        "arguments": {"x": 1},
        "parent_call_id": "exec-1",
    },
    {
        "kind": "governance_decision",
        "timestamp": 0.65,
        "agent_id": "moderator",
        "correlation_id": 3,
        "hook": "egress",
        "checkpoint": "after",
        "decision": "ALLOW",
        "reason": "action is non-destructive",
        "policy_name": "block-destructive",
    },
    {
        "kind": "tool_call_end",
        "timestamp": 0.9,
        "agent_id": "moderator",
        "call_id": "tool-1",
        "correlation_id": 3,
        "result": "ERR: timeout",
    },
    {
        "kind": "governance_decision",
        "timestamp": 0.95,
        "agent_id": "moderator",
        "correlation_id": 3,
        "hook": "ingress",
        "checkpoint": "after",
        "decision": "RETRY",
        "reason": "engine returned an error",
        "policy_name": "retry-on-error",
    },
    {
        "kind": "tool_call_start",
        "timestamp": 1.0,
        "agent_id": "moderator",
        "call_id": "tool-2",
        "correlation_id": 4,
        "tool_name": "lookup_route",
        "arguments": {"x": 1},
        "parent_call_id": "exec-1",
    },
    {
        "kind": "tool_call_end",
        "timestamp": 1.3,
        "agent_id": "moderator",
        "call_id": "tool-2",
        "correlation_id": 4,
        "result": "OK: route found",
    },
    {
        "kind": "execution_end",
        "timestamp": 1.5,
        "agent_id": "moderator",
        "call_id": "exec-1",
        "output": "Route found via alternate corridor",
        "context": {"is_entry_agent": True},
    },
    {
        "kind": "mas_call_end",
        "timestamp": 1.5,
        "agent_id": "moderator",
        "call_id": "mas-1",
        "output": "Route found via alternate corridor",
    },
    {
        "kind": "hitl_request",
        "timestamp": 1.55,
        "agent_id": "moderator",
        "correlation_id": 100,
        "question": "Proceed with booking the alternate corridor route?",
        "offered_actions": ["approve", "deny"],
    },
    {
        "kind": "hitl_resolve",
        "timestamp": 1.7,
        "agent_id": "moderator",
        "correlation_id": 100,
        "resolution": "approved",
        "answer": "yes, proceed with booking",
    },
    {"kind": "mas_call_start", "timestamp": 1.8, "agent_id": "moderator", "call_id": "mas-2"},
    {
        "kind": "execution_start",
        "timestamp": 1.8,
        "agent_id": "moderator",
        "call_id": "exec-2",
        "input": "confirm booking",
        "context": {"is_entry_agent": True},
    },
    {
        "kind": "execution_end",
        "timestamp": 2.0,
        "agent_id": "moderator",
        "call_id": "exec-2",
        "output": "Booking confirmed",
        "context": {"is_entry_agent": True},
    },
    {
        "kind": "mas_call_end",
        "timestamp": 2.0,
        "agent_id": "moderator",
        "call_id": "mas-2",
        "output": "Booking confirmed",
    },
]


def _lane(data: dict, lane_id: str) -> dict:
    return next(lane for lane in data["lanes"] if lane["laneId"] == lane_id)


def test_no_dedicated_governance_lane_is_produced() -> None:
    """Governance decisions/blocked-actions render only via per-call badges
    and ghost markers on the Calls lane — no separate consolidated lane."""
    data = build_trajectory_chart_data(_EVENTS, title="t")
    lane_ids = [lane["laneId"] for lane in data["lanes"]]
    assert "governance" not in lane_ids


def test_calls_lane_tool_bar_carries_governance_and_retry_fields() -> None:
    data = build_trajectory_chart_data(_EVENTS, title="t")
    calls = [e for e in _lane(data, "calls")["sequence"] if e["type"] == "trans"]
    tool_bars = [c for c in calls if c["callType"] == "ToolCall"]
    assert len(tool_bars) == 2

    retried = next(c for c in tool_bars if c.get("retryGroupId"))
    assert retried["retryAttempt"] == 1
    second = next(c for c in tool_bars if c.get("retryGroupId") == retried["retryGroupId"] and c is not retried)
    assert second["retryAttempt"] == 2

    assert any(d["decision"] == "RETRY" for c in tool_bars for d in c.get("governance", []))


def test_blocked_action_ghost_marker_on_a_state() -> None:
    data = build_trajectory_chart_data(_EVENTS, title="t")
    states = [e for e in _lane(data, "calls")["sequence"] if e["type"] == "state"]
    ghost_states = [s for s in states if s.get("governance")]
    assert len(ghost_states) == 1
    assert ghost_states[0]["governance"][0]["decision"] == "BLOCK"
    assert ghost_states[0]["governanceColor"] == "#ef4444"


def test_hitl_bar_shows_real_question_and_answer() -> None:
    data = build_trajectory_chart_data(_EVENTS, title="t")
    mas_bars = [e for e in _lane(data, "mas")["sequence"] if e["type"] == "trans"]
    hitl_bar = next(b for b in mas_bars if b["callType"] == "HITL")
    assert "Proceed with booking" in hitl_bar["hoverIn"]
    assert "approved" in hitl_bar["hoverOut"]
    assert "yes, proceed with booking" in hitl_bar["hoverOut"]


def test_enabled_facets_excludes_governance_layer_entirely() -> None:
    """enabled_facets={"cpr"} (governance NOT included) must remove every
    governance/retry field from the Calls lane — the same trace that
    produces all of it by default (see the tests above) produces none of it
    when the facet is turned off."""
    data = build_trajectory_chart_data(_EVENTS, title="t", enabled_facets={"cpr"})
    lane_ids = [lane["laneId"] for lane in data["lanes"]]
    assert "governance" not in lane_ids

    calls = [e for e in _lane(data, "calls")["sequence"] if e["type"] == "trans"]
    assert not any(c.get("governance") for c in calls)
    assert not any(c.get("retryGroupId") for c in calls)
    states = [e for e in _lane(data, "calls")["sequence"] if e["type"] == "state"]
    assert not any(s.get("governance") for s in states)


def test_enabled_facets_none_still_enables_every_facet_by_default() -> None:
    """The default (enabled_facets=None) must behave identically to today —
    every facet already tested above still shows up."""
    data = build_trajectory_chart_data(_EVENTS, title="t", enabled_facets=None)
    calls = [e for e in _lane(data, "calls")["sequence"] if e["type"] == "trans"]
    assert any(c.get("governance") for c in calls)
