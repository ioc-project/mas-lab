#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

from types import SimpleNamespace

from mas.ctl.ui.turn_result import turn_failure_message, turn_to_agent_result


def test_turn_failure_message_includes_governance_reason() -> None:
    turn = SimpleNamespace(
        responses=[],
        text="",
        trace=SimpleNamespace(
            client_responses=[],
            boundary_errors=[SimpleNamespace(code="GOV_BLOCK", message="beta is denied")],
        ),
    )
    mapped = turn_to_agent_result(turn)  # type: ignore[arg-type]
    assert mapped.status == "error"
    assert "beta is denied" in mapped.error_message
    assert turn_failure_message(turn, "worker") == "agent 'worker' turn failed: beta is denied"  # type: ignore[arg-type]
