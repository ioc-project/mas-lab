# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0

import pytest
from mas.lab.plots.multilevel_trajectory.records import _build_call_records


def test_build_call_records_raises_on_self_parent_call_id() -> None:
    events = [
        {
            "kind": "execution_start",
            "timestamp": 0.0,
            "agent_id": "root",
            "call_id": "exec-1",
            "parent_call_id": "exec-1",
        },
        {
            "kind": "execution_end",
            "timestamp": 1.0,
            "agent_id": "root",
            "call_id": "exec-1",
        },
    ]

    with pytest.raises(ValueError, match="self-parent"):
        _build_call_records(events)


def test_build_call_records_raises_on_orphan_end_event() -> None:
    events = [
        {
            "kind": "execution_start",
            "timestamp": 0.0,
            "agent_id": "root",
            "call_id": "exec-1",
        },
        {
            "kind": "tool_call_end",
            "timestamp": 0.5,
            "agent_id": "root",
            "call_id": "tool-404",
            "tool_name": "delegate_to_schedule_agent",
        },
        {
            "kind": "execution_end",
            "timestamp": 1.0,
            "agent_id": "root",
            "call_id": "exec-1",
        },
    ]

    with pytest.raises(ValueError, match="Orphan end event"):
        _build_call_records(events)


def test_build_call_records_raises_on_missing_call_id() -> None:
    events = [
        {
            "kind": "execution_start",
            "timestamp": 0.0,
            "agent_id": "root",
        },
        {
            "kind": "execution_end",
            "timestamp": 1.0,
            "agent_id": "root",
            "call_id": "exec-1",
        },
    ]

    with pytest.raises(ValueError, match="missing call_id"):
        _build_call_records(events)


def test_build_call_records_raises_on_duplicate_open_non_agent_call_id() -> None:
    events = [
        {
            "kind": "execution_start",
            "timestamp": 0.0,
            "agent_id": "root",
            "call_id": "exec-1",
        },
        {
            "kind": "tool_call_start",
            "timestamp": 0.1,
            "agent_id": "root",
            "call_id": "tool-1",
            "parent_call_id": "exec-1",
            "tool_name": "delegate_to_schedule_agent",
        },
        {
            "kind": "tool_call_start",
            "timestamp": 0.2,
            "agent_id": "root",
            "call_id": "tool-1",
            "parent_call_id": "exec-1",
            "tool_name": "delegate_to_concierge_agent",
        },
        {
            "kind": "execution_end",
            "timestamp": 1.0,
            "agent_id": "root",
            "call_id": "exec-1",
        },
    ]

    with pytest.raises(ValueError, match="Duplicate open call_id"):
        _build_call_records(events)
