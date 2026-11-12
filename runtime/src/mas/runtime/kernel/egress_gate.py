#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Governed egress dispatch — mirrors TLA EgressAction / GovernanceEgress* actions."""

from __future__ import annotations

from dataclasses import dataclass

from mas.runtime.boundary.gov.policy import EgressIntentView, apply_egress_modify
from mas.runtime.boundary.gov.telemetry import get_bound_observability
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.control_pipeline import control_on_idle
from mas.runtime.kernel.coord_hook import (
    coord_after_egress_allowed,
    coord_before_egress,
    coord_on_egress_blocked,
)
from mas.runtime.kernel.coupling import (
    GovDecision,
    apply_control_engine_allow,
    apply_control_tool_request,
    apply_gov_block,
    apply_gov_error,
    apply_gov_terminate,
    enter_egress_chokepoint,
    gov_plugin_boundary_error,
)
from mas.runtime.kernel.envelope import (
    EnvelopeContext,
    close_envelope,
    contract_kind_for_op,
    run_egress_authorize_envelope,
)
from mas.runtime.kernel.hitl_gate import emit_egress_hitl_pause
from mas.runtime.kernel.inflight import register_inflight
from mas.runtime.kernel.state import (
    DpState,
    QProduct,
    RunLedger,
    ScheduledEgress,
)
from mas.runtime.kernel.types import InflightKind
from mas.runtime.machines.memory import memory_on_egress_start
from mas.runtime.machines.model import model_on_abort, model_on_egress
from mas.runtime.machines.tool import tool_on_abort, tool_on_egress
from mas.runtime.machines.transport import transport_on_egress
from mas.runtime.schema.egress import (
    EgressSymbol,
    InvokeEngineIo,
    NoOp,
    RaiseBoundaryError,
)
from mas.runtime.schema.governance import GovernanceAction
from mas.runtime.schema.hitl import HitlQuestionType, HitlResolveChoice


def _destructive_for_op(op: ScheduledEgress, config: KernelConfig) -> bool:
    if op != "TOOL_CALL":
        return False
    return config.gov_trigger_destructive


def _append_synthetic_skip(
    q: QProduct,
    run: RunLedger,
    *,
    text: str = "",
    next_step: str = "STOP",
) -> None:
    run.append_placeholder(text=text, next_step=next_step)
    q.model = model_on_abort(q.model)
    q.tool = tool_on_abort(q.tool)
    q.inflight_kind = "NONE"
    q.dp = DpState.EVALUATING
    control_on_idle(q)


def _record_blocked_tool(
    q: QProduct,
    run: RunLedger,
    *,
    cid: int,
    text: str,
) -> None:
    """Write the denial as this tool's result. The call does not run.

    The model is asked again after this step (or after the rest of the batch),
    the same as after a tool that did run.
    """
    q.scheduled_egress = "NONE"
    q.inflight_kind = "NONE"
    q.tool = tool_on_abort(q.tool)
    run.append_placeholder(cid=cid, text=text, next_step="LLM_CALL")
    q.dp = DpState.EVALUATING
    control_on_idle(q)


def _apply_engine_allow(q: QProduct, view: EgressIntentView) -> None:
    """Peer posture after gov ALLOW — coupling patch + local M_tool/M_model δ only."""
    apply_control_engine_allow(q, op=view.op)  # type: ignore[arg-type]
    if view.op == "LLM_CALL":
        q.model = model_on_egress(q.model, view.op)
    elif view.op == "TOOL_CALL":
        q.tool = tool_on_egress(q.tool, view.op)
    elif view.op == "MEMORY_OP":
        q.memory = memory_on_egress_start(q.memory, view.op)
    elif view.op == "TRANSPORT_MSG":
        q.transport = transport_on_egress(q.transport, view.op)


def schedule_tool_egress(q: QProduct, run: RunLedger, config: KernelConfig) -> list[EgressSymbol]:
    """Agentic ACT → control REQUEST; run egress governance / HITL at chokepoint."""
    apply_control_tool_request(q)
    return emit_scheduled_egress(q, run, config)


def _envelope_context(
    q: QProduct,
    *,
    config: KernelConfig,
    op: ScheduledEgress,
    cid: int,
    destructive: bool,
    hitl_override: bool,
) -> EnvelopeContext:
    return EnvelopeContext(
        q=q,
        correlation_id=cid,
        contract=contract_kind_for_op(op),
        scheduled_op=op,
        observability=get_bound_observability(),
        config=config,
        tool_name=q.pending_tool_name,
        tool_arguments=dict(q.pending_tool_args or {}),
        destructive=destructive,
        hitl_gov_override=hitl_override,
    )


@dataclass
class AuthorizedEgress:
    """One call that passed the egress chokepoint and is not yet committed.

    Nothing outbound is registered for it. Either ``commit_egress`` turns it
    into an ``InvokeEngineIo`` with its wait, or ``abandon_egress`` closes its
    envelope. A batch holds these until every call has been decided, so an
    abort on a later call cannot strand an earlier one.
    """

    env_ctx: EnvelopeContext
    cid: int
    view: EgressIntentView
    decision: GovDecision


def emit_scheduled_egress(
    q: QProduct,
    run: RunLedger,
    config: KernelConfig,
) -> list[EgressSymbol]:
    outcome = authorize_scheduled_egress(q, run, config)
    if isinstance(outcome, AuthorizedEgress):
        return commit_egress(q, outcome)
    return outcome


def authorize_scheduled_egress(
    q: QProduct,
    run: RunLedger,
    config: KernelConfig,
) -> list[EgressSymbol] | AuthorizedEgress:
    """Run the chokepoint. A list is the final answer; ``AuthorizedEgress`` still needs a commit."""
    if q.scheduled_egress == "NONE":
        return [NoOp()]
    if q.tool_blacklisted and q.scheduled_egress == "TOOL_CALL":
        q.scheduled_egress = "NONE"
        _append_synthetic_skip(q, run)
        return [NoOp()]

    op = q.scheduled_egress
    if op == "TOOL_CALL" and q.hitl_block_committed:
        q.scheduled_egress = "NONE"
        _append_synthetic_skip(
            q,
            run,
            text="Tool call suppressed after operator HITL block.",
        )
        return [NoOp()]

    enter_egress_chokepoint(q)
    coord_before_egress(q)
    cid = run.allocate_correlation_id()
    destructive = _destructive_for_op(op, config)
    hitl_override = bool(
        q.hitl_gov_override or (config.hitl_once_per_turn and q.hitl_tools_approved_turn and op == "TOOL_CALL")
    )
    env_ctx = _envelope_context(
        q,
        config=config,
        op=op,
        cid=cid,
        destructive=destructive,
        hitl_override=hitl_override,
    )
    try:
        decision = run_egress_authorize_envelope(env_ctx)
    except Exception as exc:
        close_envelope(env_ctx, error="GOV_PLUGIN_ERROR")
        apply_gov_error(q)
        return [gov_plugin_boundary_error(exc)]

    view = EgressIntentView(
        op=op,
        destructive=destructive,
        correlation_id=cid,
        tool_name=q.pending_tool_name,
        tool_arguments=q.pending_tool_args,
        offered_tools=q.offered_tools,
    )

    if decision == GovDecision.HITL:
        if op == "TOOL_CALL":
            return emit_egress_hitl_pause(
                q,
                cid=cid,
                op=op,
                question="Approve tool call?",
                question_type=HitlQuestionType.CONFIRM,
                offered_actions=[
                    HitlResolveChoice.ALLOW,
                    HitlResolveChoice.BLOCK,
                    HitlResolveChoice.SKIP,
                ],
                context_data={
                    "op": op,
                    "tool": q.pending_tool_name,
                    "arguments": q.pending_tool_args or {},
                },
            )
        return emit_egress_hitl_pause(
            q,
            cid=cid,
            op=op,
            question=f"Approve {op}?",
            question_type=HitlQuestionType.MULTIPLE_CHOICE,
            offered_actions=[
                HitlResolveChoice.ALLOW,
                HitlResolveChoice.BLOCK,
                HitlResolveChoice.TERMINATE,
            ],
            context_data={"op": op, "destructive": destructive},
        )

    if decision == GovDecision.BLOCK:
        # Capture before close_envelope: ingress validate overwrites gov_reason.
        block_reason = env_ctx.gov_reason or (f"Tool '{q.pending_tool_name}' is not allowed.")
        close_envelope(env_ctx, error="GOV_BLOCK")
        if op == "TOOL_CALL":
            # This tool did not run. Do not ask the model again from here:
            # that left a wait open while other tools in the same reply were
            # still starting. Ask the model after the batch, as for a result.
            coord_on_egress_blocked(q)
            _record_blocked_tool(q, run, cid=cid, text=block_reason)
            return [NoOp()]
        apply_gov_block(q)
        coord_on_egress_blocked(q)
        return [
            RaiseBoundaryError(
                code="GOV_BLOCK",
                recoverable=True,
                message=block_reason,
                policy_name=env_ctx.policy_name or "",
            )
        ]

    if decision == GovDecision.TERMINATE:
        close_envelope(env_ctx, error="GOV_TERMINATE")
        apply_gov_terminate(q)
        return [RaiseBoundaryError(code="GOV_TERMINATE", recoverable=False)]

    if decision in {GovDecision.SKIP, GovDecision.BLACKLIST}:
        close_envelope(env_ctx, error=decision.value)
        if decision == GovDecision.BLACKLIST:
            q.tool_blacklisted = True
        q.scheduled_egress = "NONE"
        _append_synthetic_skip(q, run)
        return [NoOp()]

    if decision == GovDecision.RETRY:
        close_envelope(env_ctx, error="GOV_RETRY")
        if q.gov_retry_count >= config.max_gov_retries:
            apply_gov_block(q)
            return [RaiseBoundaryError(code="GOV_RETRY_EXHAUSTED", recoverable=True)]
        q.gov_retry_count += 1
        q.dp = DpState.EGRESS_PENDING
        return [NoOp()]

    view = apply_egress_modify(view, GovernanceAction(decision.value))
    return AuthorizedEgress(env_ctx=env_ctx, cid=cid, view=view, decision=decision)


def abandon_egress(authorized: AuthorizedEgress, *, error: str = "BATCH_ABORTED") -> None:
    """Close the envelope of a call that will not run, so start/end stay paired."""
    close_envelope(authorized.env_ctx, error=error)


def commit_egress(q: QProduct, authorized: AuthorizedEgress) -> list[EgressSymbol]:
    """Register the wait and emit the engine call for an authorized egress."""
    cid, view, decision = authorized.cid, authorized.view, authorized.decision
    q.hitl_gov_override = False
    _apply_engine_allow(q, view)
    egress_kind: InflightKind = "MODEL" if view.op == "LLM_CALL" else "TOOL"
    register_inflight(q, cid, kind=egress_kind, op=view.op)
    q.pending_engine_correlation_id = cid
    coord_after_egress_allowed(q)

    out: list[EgressSymbol] = [
        InvokeEngineIo(correlation_id=cid, op=view.op, destructive=view.destructive)  # type: ignore[arg-type]
    ]
    if decision == GovDecision.LOG:
        out.append(NoOp())
    return out
