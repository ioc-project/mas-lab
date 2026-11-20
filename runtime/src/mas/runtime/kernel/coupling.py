#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Cross-machine coupling rules — M_coord applies constraints; machines stay local.

Governance decides at the chokepoint; coupling rules propagate *allowed* side-effects
to peer machines. M_gov never embeds DP/tool δ — it publishes a decision + coupling patch.

See ``control_pipeline.py`` for the Control Mealy (tool path) and
``runtime/docs/automaton-product-model.md`` for the walkthrough.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from mas.runtime.kernel.control_pipeline import (
    ControlPhase,
    control_on_authz,
    control_on_chokepoint_enter,
    control_on_execute,
    control_on_idle,
    control_on_tool_request,
    control_on_valid,
    set_control_phase,
)
from mas.runtime.kernel.inflight import clear_inflight
from mas.runtime.kernel.state import (
    DpState,
    InflightKind,
    LifecycleState,
    ModelState,
    QProduct,
    ScheduledEgress,
    ToolState,
    end_turn,
)
from mas.runtime.machines.context import ctx_on_abort
from mas.runtime.machines.gov import gov_enter_hitl_pending, gov_on_error
from mas.runtime.machines.model import model_on_abort
from mas.runtime.machines.tool import tool_on_abort


class GovDecision(str, Enum):
    """M_gov plugin contract output at egress chokepoint."""

    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    TERMINATE = "TERMINATE"
    HITL = "HITL"
    SKIP = "SKIP"
    RETRY = "RETRY"
    LOG = "LOG"
    MODIFY = "MODIFY"
    BLACKLIST = "BLACKLIST"


@dataclass(frozen=True)
class CouplingPatch:
    """Peer-machine field updates mandated by a gov decision (not embedded in M_gov δ)."""

    dp: DpState | None = None
    tool: ToolState | None = None
    inflight_kind: InflightKind | None = None
    scheduled_egress: ScheduledEgress | None = None
    control_phase: ControlPhase | None = None


def apply_coupling_patch(q: QProduct, patch: CouplingPatch) -> None:
    if patch.dp is not None:
        q.dp = patch.dp
    if patch.tool is not None:
        q.tool = patch.tool
    if patch.inflight_kind is not None:
        q.inflight_kind = patch.inflight_kind
    if patch.scheduled_egress is not None:
        q.scheduled_egress = patch.scheduled_egress
    if patch.control_phase is not None:
        set_control_phase(q, patch.control_phase)


def coupling_for_gov_hitl_hold(*, op: ScheduledEgress) -> CouplingPatch:
    """M_gov HITL: tool waits on gov; agentic ACT blocked until gov clears."""
    _ = op
    return CouplingPatch(
        dp=DpState.AWAITING_INGRESS,
        tool=ToolState.WAIT_GOV,
        inflight_kind="TOOL",
        scheduled_egress="NONE",
        control_phase=ControlPhase.AUTHZ,
    )


def coupling_for_gov_engine_allow(*, op: ScheduledEgress) -> CouplingPatch:
    """After M_gov → ALLOW: control EXECUTE; peers await engine ingress."""
    inflight: InflightKind = "NONE"
    tool = ToolState.IDLE
    if op == "TOOL_CALL":
        inflight = "TOOL"
        tool = ToolState.EXECUTING
    elif op == "LLM_CALL":
        inflight = "MODEL"
    return CouplingPatch(
        dp=DpState.AWAITING_INGRESS,
        tool=tool,
        inflight_kind=inflight,
        scheduled_egress="NONE",
        control_phase=ControlPhase.EXECUTE if op == "TOOL_CALL" else ControlPhase.IDLE,
    )


def coupling_for_gov_block() -> CouplingPatch:
    return CouplingPatch(
        dp=DpState.IDLE,
        tool=ToolState.IDLE,
        inflight_kind="NONE",
        scheduled_egress="NONE",
        control_phase=ControlPhase.IDLE,
    )


def coupling_for_gov_terminate() -> CouplingPatch:
    return CouplingPatch(
        dp=DpState.IDLE,
        tool=ToolState.IDLE,
        inflight_kind="NONE",
        scheduled_egress="NONE",
        control_phase=ControlPhase.IDLE,
    )


def apply_gov_hitl_hold(
    q: QProduct,
    *,
    request_id: int,
    op: ScheduledEgress,
    question_type: str = "CONFIRM",
    call_id: str = "",
) -> CouplingPatch:
    """M_gov enters HITL_PENDING; M_coord applies peer patches (not M_gov)."""
    gov_enter_hitl_pending(
        q,
        request_id=request_id,
        pending_schedule=op,
        question_type=question_type,
        tool_name=q.pending_tool_name if op == "TOOL_CALL" else "",
        tool_arguments=q.pending_tool_args if op == "TOOL_CALL" else None,
        call_id=call_id,
    )
    patch = coupling_for_gov_hitl_hold(op=op)
    apply_coupling_patch(q, patch)
    return patch


def apply_gov_block(q: QProduct) -> CouplingPatch:
    patch = coupling_for_gov_block()
    apply_coupling_patch(q, patch)
    q.ctx = ctx_on_abort(q.ctx)
    q.model = model_on_abort(q.model)
    q.tool = tool_on_abort(q.tool)
    control_on_idle(q)
    return patch


def apply_gov_terminate(q: QProduct) -> CouplingPatch:
    patch = coupling_for_gov_terminate()
    q.ctrl = LifecycleState.STOPPED
    end_turn(q)
    return patch


def apply_gov_error(q: QProduct) -> CouplingPatch:
    """Plugin exception at M_gov: product ERROR; abort peers; drop scheduled egress.

    Fail closed — same coupling as a block (no further engine I/O), but M_gov
    (and any in-flight model/tool) land in ERROR rather than IDLE/BLOCKED.
    """
    was_calling = q.model == ModelState.CALLING
    was_executing = q.tool == ToolState.EXECUTING
    gov_on_error(q)
    patch = coupling_for_gov_block()
    apply_coupling_patch(q, patch)
    clear_inflight(q)
    q.ctx = ctx_on_abort(q.ctx)
    q.model = ModelState.ERROR if was_calling else model_on_abort(q.model)
    q.tool = ToolState.ERROR if was_executing else tool_on_abort(q.tool)
    control_on_idle(q)
    from mas.runtime.kernel.coord_hook import coord_on_gov_error

    coord_on_gov_error(q)
    return patch


def gov_plugin_boundary_error(exc: BaseException):
    """Σ_out for a governance plugin exception — kernel error, not a Python raise."""
    from mas.runtime.schema.egress import RaiseBoundaryError

    return RaiseBoundaryError(
        code="GOV_PLUGIN_ERROR",
        recoverable=False,
        message=str(exc) or type(exc).__name__,
    )


def apply_control_tool_request(q: QProduct) -> None:
    """Agentic ACT schedules tool → control pipeline REQUEST (coupling entry)."""
    control_on_tool_request(q)
    q.scheduled_egress = "TOOL_CALL"
    q.dp = DpState.EGRESS_PENDING


def enter_egress_chokepoint(q: QProduct) -> None:
    """REQUEST → INGRESS → AUTHZ at the gov chokepoint."""
    control_on_chokepoint_enter(q)
    control_on_authz(q)


def apply_control_engine_allow(q: QProduct, *, op: ScheduledEgress) -> CouplingPatch:
    """AUTHZ cleared → EXECUTE posture on peers."""
    patch = coupling_for_gov_engine_allow(op=op)
    apply_coupling_patch(q, patch)
    if op == "TOOL_CALL":
        control_on_execute(q)
    return patch


def apply_control_valid(q: QProduct) -> None:
    """Control pipeline complete — agentic loop may proceed to CONTEXT."""
    control_on_valid(q)
    control_on_idle(q)


def gov_decision_from_action(action: str) -> GovDecision:
    return GovDecision(action)


def coupling_for_ingress_deny() -> CouplingPatch:
    return CouplingPatch(
        dp=DpState.IDLE,
        inflight_kind="NONE",
        scheduled_egress="NONE",
        control_phase=ControlPhase.IDLE,
    )


def apply_ingress_deny(q: QProduct) -> CouplingPatch:
    patch = coupling_for_ingress_deny()
    apply_coupling_patch(q, patch)
    q.ctx = ctx_on_abort(q.ctx)
    clear_inflight(q)
    from mas.runtime.machines.memory import memory_on_reset
    from mas.runtime.machines.transport import transport_on_reset

    q.memory = memory_on_reset(q.memory)
    q.transport = transport_on_reset(q.transport)
    control_on_idle(q)
    return patch


def coupling_for_lifecycle_pause() -> CouplingPatch:
    return CouplingPatch(
        dp=DpState.IDLE,
        inflight_kind="NONE",
        scheduled_egress="NONE",
        control_phase=ControlPhase.IDLE,
    )


def coupling_for_lifecycle_abort() -> CouplingPatch:
    return coupling_for_lifecycle_pause()


def apply_lifecycle_pause(q: QProduct) -> CouplingPatch:
    """Pause stops the turn: no engine wait or pending call survives it. A pending human answer does: the
    hold stays, and its operation is scheduled only once it is answered and the lifecycle runs again."""
    patch = coupling_for_lifecycle_pause()
    end_turn(q, keep_holds=True)
    return patch


def apply_lifecycle_abort(q: QProduct) -> CouplingPatch:
    q.ctrl = LifecycleState.STOPPED
    patch = coupling_for_lifecycle_pause()
    end_turn(q)
    q.ctx = ctx_on_abort(q.ctx)
    return patch
