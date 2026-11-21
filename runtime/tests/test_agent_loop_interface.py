#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The agent loop interface is a library plugin type: it adds no boundary slot and no kernel symbol."""

from __future__ import annotations

import pytest
from mas.runtime.boundary.loop import AgentLoop, LoopDenied, LoopError, LoopReply, LoopTask
from mas.runtime.harness.catalog import (
    BOUNDARY_SLOTS,
    KERNEL_OPS,
    SPEC_IDENTITY_TYPES,
    UnknownBoundarySlotError,
    assert_boundary_slot,
    classify_plugin_type,
)


def test_agent_loop_is_a_library_type_not_a_boundary_slot() -> None:
    assert classify_plugin_type("agent_loop") == "library"
    assert classify_plugin_type("agent-loop") == "library"
    assert "agent_loop" not in BOUNDARY_SLOTS
    assert "agent_loop" not in SPEC_IDENTITY_TYPES
    assert "agent_loop" not in KERNEL_OPS
    with pytest.raises(UnknownBoundarySlotError):
        assert_boundary_slot("agent_loop")


def test_a_task_is_immutable_and_its_headers_are_a_copy() -> None:
    headers = {"traceparent": "00-aa-bb-01"}
    task = LoopTask(text="x", headers=headers)
    headers["traceparent"] = "changed"
    assert task.headers["traceparent"] == "00-aa-bb-01"
    with pytest.raises(TypeError):
        task.headers["x"] = "y"  # type: ignore[index]
    with pytest.raises(AttributeError):
        task.text = "y"  # type: ignore[misc]


def test_a_reply_defaults_to_nothing_and_a_denial_is_a_loop_error() -> None:
    assert LoopReply() == LoopReply(text="", context_id="", artifacts=(), task_state="", stream=(), events=())
    denied = LoopDenied("BLOCK", "policy-x", "why")
    assert isinstance(denied, LoopError)
    assert (denied.decision, denied.policy, denied.reason) == ("BLOCK", "policy-x", "why")


def test_the_protocol_is_the_whole_contract() -> None:
    class Minimal:
        loop_id = "minimal"

        def run(self, task: LoopTask) -> LoopReply:
            return LoopReply(text=task.text.upper())

        def close(self) -> None:
            return None

    loop: AgentLoop = Minimal()
    assert loop.run(LoopTask(text="hi")).text == "HI"
