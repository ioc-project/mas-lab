#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The agent runner interface is a library plugin type: it adds no boundary slot and no kernel symbol."""

from __future__ import annotations

import pytest
from mas.runtime.boundary.runner import AgentRunner, RunnerDenied, RunnerError, RunnerReply, RunnerTask
from mas.runtime.harness.catalog import (
    BOUNDARY_SLOTS,
    KERNEL_OPS,
    SPEC_IDENTITY_TYPES,
    UnknownBoundarySlotError,
    assert_boundary_slot,
    classify_plugin_type,
)


def test_agent_runner_is_a_library_type_not_a_boundary_slot() -> None:
    assert classify_plugin_type("agent_runner") == "library"
    assert classify_plugin_type("agent-runner") == "library"
    assert "agent_runner" not in BOUNDARY_SLOTS
    assert "agent_runner" not in SPEC_IDENTITY_TYPES
    assert "agent_runner" not in KERNEL_OPS
    with pytest.raises(UnknownBoundarySlotError):
        assert_boundary_slot("agent_runner")


def test_a_task_is_immutable_and_its_headers_are_a_copy() -> None:
    headers = {"traceparent": "00-aa-bb-01"}
    task = RunnerTask(text="x", headers=headers)
    headers["traceparent"] = "changed"
    assert task.headers["traceparent"] == "00-aa-bb-01"
    with pytest.raises(TypeError):
        task.headers["x"] = "y"  # type: ignore[index]
    with pytest.raises(AttributeError):
        task.text = "y"  # type: ignore[misc]


def test_a_reply_defaults_to_nothing_and_a_denial_is_a_runner_error() -> None:
    assert RunnerReply() == RunnerReply(text="", context_id="", artifacts=(), task_state="", stream=(), events=())
    denied = RunnerDenied("BLOCK", "policy-x", "why")
    assert isinstance(denied, RunnerError)
    assert (denied.decision, denied.policy, denied.reason) == ("BLOCK", "policy-x", "why")


def test_the_protocol_is_the_whole_contract() -> None:
    class Minimal:
        runner_id = "minimal"

        def run(self, task: RunnerTask) -> RunnerReply:
            return RunnerReply(text=task.text.upper())

        def close(self) -> None:
            return None

    runner: AgentRunner = Minimal()
    assert runner.run(RunnerTask(text="hi")).text == "HI"
