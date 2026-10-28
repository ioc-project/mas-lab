#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Telemetry handoff detection uses the shared helper."""

from mas.library.telemetry.conversion.delegation import delegate_target_agent


def test_legacy_delegate_to_name_is_a_handoff() -> None:
    assert delegate_target_agent("delegate_to_schedule_agent") == "schedule_agent"


def test_user_tool_named_send_is_not_a_handoff() -> None:
    assert (
        delegate_target_agent(
            "send",
            {"tool_name": "send", "arguments": {"target": "worker", "message": "hi"}},
        )
        == ""
    )


def test_tool_with_target_argument_is_not_a_handoff() -> None:
    assert (
        delegate_target_agent(
            "book_flight",
            {"tool_name": "book_flight", "arguments": {"target": "paris"}},
        )
        == ""
    )


def test_recorded_agent_comm_semantics_are_a_handoff() -> None:
    assert (
        delegate_target_agent(
            "send",
            {
                "tool_name": "send",
                "arguments": {"target": "worker"},
                "semantics": {
                    "concept": "agent_comm",
                    "op": "send",
                    "subject_arg": "target",
                },
            },
        )
        == "worker"
    )
