#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""M_gov — governance Mealy δ (Governance.tla). HITL lives here only."""

from __future__ import annotations

from mas.runtime.kernel.outbound_waits import dismiss_outbound_wait, register_outbound_wait
from mas.runtime.kernel.state import QProduct, ScheduledEgress
from mas.runtime.kernel.types import GovState, HitlHold


def gov_is_hitl_pending(q: QProduct) -> bool:
    return bool(q.hitl_holds)


def gov_first_hold_id(q: QProduct) -> int:
    """The oldest pending request id, 0 when nothing is held."""
    return min(q.hitl_holds, default=0)


def gov_enter_hitl_pending(
    q: QProduct,
    *,
    request_id: int,
    pending_schedule: ScheduledEgress,
    question_type: str = "CONFIRM",
    tool_name: str = "",
    tool_arguments: dict | None = None,
    ingress_return: dict | None = None,
    call_id: str = "",
) -> None:
    """Add one hold under ``request_id``; holds that are already pending stay as they are."""
    q.gov_state = GovState.HITL_PENDING.value
    q.hitl_holds[request_id] = HitlHold(
        request_id=request_id,
        pending_schedule=pending_schedule,
        question_type=question_type,
        tool_name=tool_name,
        tool_arguments=dict(tool_arguments or {}),
        ingress_return=dict(ingress_return or {}),
        call_id=call_id,
    )
    register_outbound_wait(q, request_id, kind="HITL", op=pending_schedule)


def gov_clear_hitl(q: QProduct, request_id: int | None = None) -> None:
    """Drop one hold, or every hold when no id is given. Governance leaves HITL_PENDING with the last one."""
    for rid in [request_id] if request_id is not None else list(q.hitl_holds):
        q.hitl_holds.pop(rid, None)
        dismiss_outbound_wait(q, rid)
    if not q.hitl_holds and q.gov_state == GovState.HITL_PENDING.value:
        q.gov_state = GovState.IDLE.value


def gov_on_hitl_schedule(q: QProduct) -> None:
    if not q.hitl_holds:
        q.gov_state = GovState.AUTHZ_EGRESS.value


def gov_on_egress_allowed(q: QProduct) -> None:
    q.gov_state = GovState.VALIDATING.value


def gov_on_idle(q: QProduct) -> None:
    q.gov_state = GovState.IDLE.value


def gov_on_error(q: QProduct) -> None:
    q.gov_state = GovState.ERROR.value
