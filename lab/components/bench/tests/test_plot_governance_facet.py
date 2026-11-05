#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""multilevel_trajectory/governance.py — facet function coverage.

None of these had a dedicated unit test before (confirmed by grep — only
exercised indirectly through end-to-end plot-generation tests, which cannot
catch a wrong dict key, an off-by-one in retry-attempt numbering, or a wrong
severity ranking).
"""

from __future__ import annotations

from mas.lab.plots.multilevel_trajectory.governance import (
    _collect_blocked_actions,
    _collect_governance_decisions,
    _collect_hitl_exchanges,
    _collect_retry_chains,
    _governance_severity,
)

# --- _collect_governance_decisions ------------------------------------------


def test_collect_governance_decisions_matches_by_agent_and_correlation_id() -> None:
    events = [
        {
            "kind": "governance_decision",
            "agent_id": "a",
            "correlation_id": 3,
            "hook": "egress",
            "checkpoint": "after",
            "decision": "ALLOW",
            "reason": "ok",
            "policy_name": "p1",
        },
    ]
    records = [{"call_id": "tool-1", "agent_id": "a", "correlation_id": 3}]
    out = _collect_governance_decisions(events, records)
    assert out == {
        "tool-1": [{"hook": "egress", "checkpoint": "after", "decision": "ALLOW", "reason": "ok", "policyName": "p1"}]
    }


def test_collect_governance_decisions_ignores_events_with_no_decision() -> None:
    events = [{"kind": "governance_decision", "agent_id": "a", "correlation_id": 3, "decision": ""}]
    records = [{"call_id": "tool-1", "agent_id": "a", "correlation_id": 3}]
    assert _collect_governance_decisions(events, records) == {}


def test_collect_governance_decisions_no_matching_record_is_dropped() -> None:
    events = [{"kind": "governance_decision", "agent_id": "a", "correlation_id": 99, "decision": "ALLOW"}]
    records = [{"call_id": "tool-1", "agent_id": "a", "correlation_id": 3}]
    assert _collect_governance_decisions(events, records) == {}


# --- _collect_hitl_exchanges -------------------------------------------------


def test_collect_hitl_exchanges_pairs_request_and_resolve() -> None:
    events = [
        {"kind": "hitl_request", "correlation_id": 5, "question": "Proceed?", "offered_actions": ["yes", "no"]},
        {"kind": "hitl_resolve", "correlation_id": 5, "resolution": "approved", "answer": "yes"},
    ]
    out = _collect_hitl_exchanges(events)
    assert out == {
        5: {"question": "Proceed?", "offeredActions": ["yes", "no"], "resolution": "approved", "answer": "yes"}
    }


def test_collect_hitl_exchanges_request_without_resolve_stays_unresolved() -> None:
    events = [{"kind": "hitl_request", "correlation_id": 5, "question": "Proceed?"}]
    out = _collect_hitl_exchanges(events)
    assert out[5]["resolution"] == ""
    assert out[5]["answer"] == ""


# --- _collect_blocked_actions -------------------------------------------------


def test_collect_blocked_actions_finds_terminal_decision_with_no_live_call() -> None:
    events = [
        {
            "kind": "governance_decision",
            "agent_id": "a",
            "correlation_id": 2,
            "timestamp": 0.6,
            "hook": "egress",
            "checkpoint": "after",
            "decision": "BLOCK",
            "reason": "blocked",
            "policy_name": "p1",
        },
    ]
    records: list[dict] = []  # correlation_id 2 never produced a live call
    out = _collect_blocked_actions(events, records)
    assert out == [{"agent_id": "a", "ts": 0.6, "decision": "BLOCK", "reason": "blocked", "policyName": "p1"}]


def test_collect_blocked_actions_skips_when_call_actually_ran() -> None:
    events = [
        {
            "kind": "governance_decision",
            "agent_id": "a",
            "correlation_id": 3,
            "timestamp": 0.6,
            "hook": "egress",
            "checkpoint": "after",
            "decision": "BLOCK",
        },
    ]
    records = [{"call_id": "tool-1", "agent_id": "a", "correlation_id": 3}]
    assert _collect_blocked_actions(events, records) == []


def test_collect_blocked_actions_ignores_non_terminal_decisions() -> None:
    events = [
        {
            "kind": "governance_decision",
            "agent_id": "a",
            "correlation_id": 2,
            "timestamp": 0.6,
            "hook": "egress",
            "checkpoint": "after",
            "decision": "ALLOW",
        },
    ]
    assert _collect_blocked_actions(events, []) == []


def test_collect_blocked_actions_ignores_ingress_hook() -> None:
    """Only egress decisions can pre-empt a call before it runs — an ingress
    BLOCK happens after the engine already ran, so there IS a live record."""
    events = [
        {
            "kind": "governance_decision",
            "agent_id": "a",
            "correlation_id": 2,
            "timestamp": 0.6,
            "hook": "ingress",
            "checkpoint": "after",
            "decision": "BLOCK",
        },
    ]
    assert _collect_blocked_actions(events, []) == []


# --- _collect_retry_chains ----------------------------------------------------


def test_collect_retry_chains_links_two_attempts() -> None:
    events = [
        {"kind": "governance_decision", "agent_id": "a", "hook": "ingress", "decision": "RETRY", "timestamp": 0.35},
    ]
    records = [
        {
            "call_id": "tool-1",
            "agent_id": "a",
            "call_type": "ToolCall",
            "tool_name": "flaky",
            "model": "",
            "start_ts": 0.1,
            "end_ts": 0.3,
        },
        {
            "call_id": "tool-2",
            "agent_id": "a",
            "call_type": "ToolCall",
            "tool_name": "flaky",
            "model": "",
            "start_ts": 0.4,
            "end_ts": 0.6,
        },
    ]
    out = _collect_retry_chains(events, records)
    assert out["tool-1"] == {"groupId": "tool-1", "attempt": 1}
    assert out["tool-2"] == {"groupId": "tool-1", "attempt": 2}


def test_collect_retry_chains_three_attempts_numbered_in_order() -> None:
    events = [
        {"kind": "governance_decision", "agent_id": "a", "hook": "ingress", "decision": "RETRY", "timestamp": 0.35},
        {"kind": "governance_decision", "agent_id": "a", "hook": "ingress", "decision": "RETRY", "timestamp": 0.65},
    ]
    records = [
        {
            "call_id": "t1",
            "agent_id": "a",
            "call_type": "ToolCall",
            "tool_name": "flaky",
            "model": "",
            "start_ts": 0.1,
            "end_ts": 0.3,
        },
        {
            "call_id": "t2",
            "agent_id": "a",
            "call_type": "ToolCall",
            "tool_name": "flaky",
            "model": "",
            "start_ts": 0.4,
            "end_ts": 0.6,
        },
        {
            "call_id": "t3",
            "agent_id": "a",
            "call_type": "ToolCall",
            "tool_name": "flaky",
            "model": "",
            "start_ts": 0.7,
            "end_ts": 0.9,
        },
    ]
    out = _collect_retry_chains(events, records)
    assert [out[c]["attempt"] for c in ("t1", "t2", "t3")] == [1, 2, 3]
    assert out["t1"]["groupId"] == out["t2"]["groupId"] == out["t3"]["groupId"] == "t1"


def test_collect_retry_chains_no_retry_decision_leaves_calls_unlinked() -> None:
    records = [
        {
            "call_id": "t1",
            "agent_id": "a",
            "call_type": "ToolCall",
            "tool_name": "flaky",
            "model": "",
            "start_ts": 0.1,
            "end_ts": 0.3,
        },
        {
            "call_id": "t2",
            "agent_id": "a",
            "call_type": "ToolCall",
            "tool_name": "flaky",
            "model": "",
            "start_ts": 0.4,
            "end_ts": 0.6,
        },
    ]
    assert _collect_retry_chains([], records) == {}


def test_collect_retry_chains_different_tool_not_linked() -> None:
    """A RETRY between two calls of DIFFERENT tools is not a retry chain —
    same_op requires matching call_type/tool_name/model."""
    events = [
        {"kind": "governance_decision", "agent_id": "a", "hook": "ingress", "decision": "RETRY", "timestamp": 0.35},
    ]
    records = [
        {
            "call_id": "t1",
            "agent_id": "a",
            "call_type": "ToolCall",
            "tool_name": "flaky",
            "model": "",
            "start_ts": 0.1,
            "end_ts": 0.3,
        },
        {
            "call_id": "t2",
            "agent_id": "a",
            "call_type": "ToolCall",
            "tool_name": "other",
            "model": "",
            "start_ts": 0.4,
            "end_ts": 0.6,
        },
    ]
    assert _collect_retry_chains(events, records) == {}


# --- _governance_severity ----------------------------------------------------


def test_governance_severity_picks_worst_decision() -> None:
    gdata = [
        {"decision": "ALLOW"},
        {"decision": "RETRY"},
    ]
    decision, call_type, color = _governance_severity(gdata)
    assert decision == "RETRY"
    assert call_type == "GovernanceCaution"
    assert color == "#f59e0b"


def test_governance_severity_block_outranks_caution() -> None:
    gdata = [{"decision": "RETRY"}, {"decision": "BLOCK"}]
    decision, call_type, color = _governance_severity(gdata)
    assert decision == "BLOCK"
    assert call_type == "GovernanceBlock"
    assert color == "#ef4444"


def test_governance_severity_all_allow_is_lowest() -> None:
    gdata = [{"decision": "ALLOW"}, {"decision": "LOG"}]
    decision, call_type, color = _governance_severity(gdata)
    assert call_type == "GovernanceAllow"
    assert color == "#64748b"


def test_governance_severity_unrecognized_decision_ranks_above_allow() -> None:
    """An unrecognized future decision value should not be silently treated
    as harmless — it ranks above ALLOW/LOG (rank 1) so it's visible."""
    decision, call_type, color = _governance_severity([{"decision": "SOMETHING_NEW"}])
    assert call_type == "GovernanceCaution"
