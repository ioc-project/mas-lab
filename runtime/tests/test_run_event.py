#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Run-log factories and newest-row identity."""

from __future__ import annotations

from mas.runtime.kernel.run_event import (
    from_engine_return,
    max_event_seq,
    new_run_events,
    synthetic_tool_result,
)
from mas.runtime.kernel.state import RunEvent, RunLedger
from mas.runtime.schema.governance import GovernanceAction
from mas.runtime.schema.ingress import EngineIoReturn


def test_from_engine_return_copies_tool_payload() -> None:
    event = from_engine_return(
        EngineIoReturn(
            correlation_id=4,
            response_kind="MODEL_TEXT",
            next_step="TOOL_CALL",
            tool_name="activate_skill",
            tool_arguments={"name": "x"},
            text="",
        )
    )
    assert event.correlation_id == 4
    assert event.tool_name == "activate_skill"
    assert event.tool_arguments == {"name": "x"}
    assert event.seq == 0


def test_synthetic_tool_result_is_empty_skip_by_default() -> None:
    row = synthetic_tool_result(cid=9)
    assert row.response_kind == "TOOL_RESULT"
    assert row.text == ""
    assert row.next_step == "LLM_CALL"


def test_ledger_since_is_seq_not_list_tail() -> None:
    run = RunLedger()
    run.append(RunEvent(correlation_id=1, response_kind="TOOL_RESULT", next_step="LLM_CALL", text="a"))
    run.append(RunEvent(correlation_id=2, response_kind="TOOL_RESULT", next_step="LLM_CALL", text="b"))
    run.append(RunEvent(correlation_id=4, response_kind="TOOL_RESULT", next_step="LLM_CALL", text="d"))
    seen = run.max_seq()
    run.append_inflight(RunEvent(correlation_id=3, response_kind="TOOL_RESULT", next_step="LLM_CALL", text="c"))
    delta = run.since(seen)
    assert [(row.correlation_id, row.text) for row in delta] == [(3, "c")]
    assert run.events[-1].correlation_id == 4


def test_append_placeholder_stamps_seq() -> None:
    run = RunLedger()
    run.append(RunEvent(correlation_id=1, response_kind="MODEL_TEXT", next_step="TOOL_CALL"))
    row = run.append_placeholder(text="Operator skipped tool", next_step="LLM_CALL")
    assert row.response_kind == "TOOL_RESULT"
    assert row.seq == 2
    assert run.since(1) == [row]
    assert row.placeholder and not run.events[0].placeholder  # a real engine row is not a placeholder
    from mas.runtime.kernel.state_serialize import run_from_dict, run_to_dict

    again = run_from_dict(run_to_dict(run))
    assert [r.placeholder for r in again.events] == [False, True]
    assert "placeholder" not in run_to_dict(run)["events"][0]  # existing snapshots keep their shape


def test_new_run_events_empty_log() -> None:
    assert max_event_seq(None) == 0
    assert new_run_events(None, 0) == []
    assert new_run_events([], 0) == []


def test_ledger_latest_is_newest_seq_not_list_tail() -> None:
    run = RunLedger()
    run.append(RunEvent(correlation_id=1, response_kind="TOOL_RESULT", next_step="LLM_CALL", text="a"))
    run.append(RunEvent(correlation_id=2, response_kind="TOOL_RESULT", next_step="LLM_CALL", text="b"))
    run.append(RunEvent(correlation_id=4, response_kind="TOOL_RESULT", next_step="LLM_CALL", text="d"))
    run.append_inflight(RunEvent(correlation_id=3, response_kind="TOOL_RESULT", next_step="LLM_CALL", text="c"))
    assert run.events[-1].correlation_id == 4
    latest = run.latest()
    assert latest is not None
    assert latest.correlation_id == 3
    assert latest.text == "c"


def test_every_governance_action_is_classified_for_append() -> None:
    """A new enum member must be listed here with an append/no-append test."""
    classified = {
        GovernanceAction.ALLOW,
        GovernanceAction.LOG,
        GovernanceAction.MODIFY,
        GovernanceAction.SKIP,
        GovernanceAction.BLACKLIST,
        GovernanceAction.BLOCK,
        GovernanceAction.HITL,
        GovernanceAction.RETRY,
        GovernanceAction.BACKTRACK,
        GovernanceAction.TERMINATE,
    }
    assert set(GovernanceAction) == classified
