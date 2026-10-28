#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared handoff helper: semantics or legacy name, never a guessed target."""

from mas.runtime.boundary.delegation.handoff import handoff_target


def test_legacy_delegate_to_name_is_a_handoff() -> None:
    assert handoff_target("delegate_to_researcher") == "researcher"
    assert handoff_target("delegate_to_") == ""
    assert handoff_target("web_search") == ""


def test_user_tool_named_send_is_not_a_handoff() -> None:
    assert handoff_target("send", arguments={"target": "worker", "message": "hi"}) == ""
    assert (
        handoff_target(
            "send",
            event={"tool_name": "send", "arguments": {"target": "worker"}},
        )
        == ""
    )


def test_tool_with_target_argument_is_not_a_handoff() -> None:
    assert (
        handoff_target(
            "book_flight",
            arguments={"target": "paris", "when": "tomorrow"},
        )
        == ""
    )


def test_recorded_agent_comm_semantics_are_a_handoff() -> None:
    assert (
        handoff_target(
            "send",
            arguments={"target": "worker", "message": "hi"},
            semantics={"concept": "agent_comm", "op": "send", "subject_arg": "target"},
        )
        == "worker"
    )
    assert (
        handoff_target(
            "send",
            event={
                "tool_name": "send",
                "args": {"target": "worker"},
                "semantics": {"concept": "agent_comm", "subject": "worker"},
            },
        )
        == "worker"
    )
