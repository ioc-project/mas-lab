#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Map SessionController turns to UI ``AgentTurnResult`` fields (mas-lab Canvas)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from mas.ctl.session.controller import TurnResult


@dataclass(frozen=True)
class AgentTurnResult:
    """Structured turn outcome — matches mas-lab controller / Canvas ``ChatMessage``."""

    status: Literal["ok", "error"]
    response: str = ""
    error_message: str = ""
    error_detail: str = ""


def turn_to_agent_result(turn: TurnResult) -> AgentTurnResult:
    """Convert a ctl turn trace into UI job fields."""
    responses = turn.responses or list(turn.trace.client_responses)
    for resp in responses:
        if getattr(resp, "finish_reason", "stop") == "error" and resp.content.strip():
            return AgentTurnResult(
                status="error",
                error_message=resp.content.strip(),
                error_detail=resp.content.strip(),
            )

    if turn.trace.boundary_errors:
        err = turn.trace.boundary_errors[-1]
        code = getattr(err, "code", str(err))
        message = getattr(err, "message", "") or str(code)
        return AgentTurnResult(
            status="error",
            error_message=message.strip() or str(code),
            error_detail=str(code),
        )

    text = turn.text
    if text:
        return AgentTurnResult(status="ok", response=text)

    return AgentTurnResult(
        status="error",
        error_message="Agent returned no answer.",
        error_detail="The kernel finished without client text or boundary errors.",
    )


def turn_failed(turn: TurnResult) -> bool:
    """True when the turn ended in an error state."""
    return turn_to_agent_result(turn).status == "error"


def turn_failure_message(turn: TurnResult, agent_id: str) -> str:
    """Reason a delegated turn is withheld, including the policy or boundary text."""
    mapped = turn_to_agent_result(turn)
    reason = (mapped.error_message or mapped.error_detail or "").strip()
    if reason:
        return f"agent {agent_id!r} turn failed: {reason}"
    return f"agent {agent_id!r} turn failed"
