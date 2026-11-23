#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Apply operator HITL resolution — M_gov ingress only (HitlResolve* in TLA)."""

from __future__ import annotations

from collections.abc import Callable

from mas.runtime.boundary.gov.plugin import verify_hitl_resolve
from mas.runtime.kernel.client_response import client_response_from_run
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.egress_gate import emit_scheduled_egress
from mas.runtime.kernel.inflight import clear_inflight
from mas.runtime.kernel.ingress_step import commit_engine_io_return
from mas.runtime.kernel.state import (
    CtxState,
    DpState,
    LifecycleState,
    MemoryState,
    ModelState,
    QProduct,
    RunLedger,
    SessionState,
    TransportState,
    end_turn,
)
from mas.runtime.kernel.types import HitlHold
from mas.runtime.machines.gov import gov_clear_hitl, gov_on_hitl_schedule
from mas.runtime.machines.tool import tool_on_abort
from mas.runtime.schema.egress import EgressSymbol, EmitHitlRequest, NoOp
from mas.runtime.schema.hitl import HitlQuestionType, HitlResolveChoice
from mas.runtime.schema.ingress import EngineIoReturn, HitlResolve


def _operator_tool_text(*, blocked: bool, tool_ran: bool, tool_name: str, steering: str) -> str:
    if not blocked and steering:
        return steering
    verb = "blocked" if blocked else "skipped"
    if tool_ran:
        base = f"Operator {verb} tool result for '{tool_name}' via HITL."
    else:
        base = f"Operator {verb} tool '{tool_name}' via HITL."
    if blocked and steering:
        return f"{base} {steering}"
    return base


def _end_turn_with_denial(q: QProduct, run: RunLedger, text: str) -> list[EgressSymbol]:
    """BLOCK refuses the operation: no follow-up LLM call, the user sees the denial, the turn ends with its holds."""
    end_turn(q)
    return [client_response_from_run(run, fallback=text)]


def _modified_ingress_return(pending: EngineIoReturn, text: str, *, blocked: bool) -> EngineIoReturn:
    stop = blocked or pending.response_kind != "TOOL_RESULT"
    update: dict = {"text": text, "next_step": "STOP" if stop else "LLM_CALL"}
    if pending.response_kind != "TOOL_RESULT":
        update["response_kind"] = "MODEL_TEXT"
    return pending.model_copy(update=update)


def _resolve_ingress_hitl(
    q: QProduct,
    run: RunLedger,
    event: HitlResolve,
    hold: HitlHold,
    *,
    config: KernelConfig,
    evaluate: Callable,
) -> list[EgressSymbol]:
    pending = EngineIoReturn.model_validate(dict(hold.ingress_return))
    resolution = event.resolution

    if resolution in {HitlResolveChoice.ALLOW, HitlResolveChoice.SCHEDULE}:
        q.hitl_results_approved_turn = True
        gov_clear_hitl(q, hold.request_id)
        return commit_engine_io_return(
            q,
            run,
            pending,
            config=config,
            evaluate=evaluate,
        )

    if resolution not in {HitlResolveChoice.BLOCK, HitlResolveChoice.SKIP}:
        return [NoOp()]

    blocked = resolution == HitlResolveChoice.BLOCK
    text = _operator_tool_text(
        blocked=blocked,
        tool_ran=True,
        tool_name=hold.tool_name or "tool",
        steering=event.steering_text,
    )
    modified = _modified_ingress_return(pending, text, blocked=blocked)
    if blocked:
        # A denial ends the turn: the sibling holds and the calls still running go with it.
        gov_clear_hitl(q)
        clear_inflight(q)
    else:
        gov_clear_hitl(q, hold.request_id)

    def _evaluate_block(q_inner: QProduct, run_inner: RunLedger, **_: object) -> list[EgressSymbol]:
        return _end_turn_with_denial(q_inner, run_inner, text)

    return commit_engine_io_return(
        q,
        run,
        modified,
        config=config,
        evaluate=_evaluate_block if blocked else evaluate,
    )


def _pending_egress_request(hold: HitlHold) -> EmitHitlRequest:
    """The hold being answered, rebuilt from the hold (the emitted request is not kept)."""
    pending = hold.pending_schedule
    context: dict = {"op": pending}
    if pending == "TOOL_CALL":
        context.update(tool=hold.tool_name, arguments=hold.tool_arguments)
    return EmitHitlRequest(
        request_id=hold.request_id,
        question_type=HitlQuestionType(hold.question_type or HitlQuestionType.CONFIRM.value),
        pending_schedule=pending,
        context_data=context,
    )


def _refuse_unverified_allow(hold: HitlHold, event: HitlResolve, *, config: KernelConfig) -> HitlResolve:
    """An ALLOW answer takes effect only if the governance plugin accepts it; a refusal turns it into BLOCK."""
    verdict = verify_hitl_resolve(config.egress_governance_plugin, _pending_egress_request(hold), event)
    if verdict is None:
        return event
    return event.model_copy(
        update={
            "resolution": HitlResolveChoice.BLOCK,
            "operator_context": {**event.operator_context, "steering": verdict[2]},
        }
    )


def apply_hitl_resolve(
    q: QProduct,
    run: RunLedger,
    event: HitlResolve,
    *,
    config: KernelConfig,
    evaluate: Callable | None = None,
) -> list[EgressSymbol]:
    """Governance resolves HITL; DP stays AWAITING_INGRESS (waiting on tool chain)."""
    hold = q.hitl_holds.get(event.request_id)
    if hold is None:
        return [NoOp()]

    if hold.is_ingress:
        if evaluate is None:
            return [NoOp()]
        return _resolve_ingress_hitl(q, run, event, hold, config=config, evaluate=evaluate)

    pending = hold.pending_schedule
    if event.resolution in {HitlResolveChoice.ALLOW, HitlResolveChoice.SCHEDULE} and pending != "NONE":
        event = _refuse_unverified_allow(hold, event, config=config)
    resolution = event.resolution
    # The held call is the one this answer is about, whatever the state was moved to since.
    q.pending_tool_name, q.pending_tool_args = hold.tool_name, dict(hold.tool_arguments)

    if resolution in {HitlResolveChoice.ALLOW, HitlResolveChoice.SCHEDULE} and pending != "NONE":
        q.scheduled_egress = pending
        q.hitl_gov_override = True
        q.call_cid = hold.request_id  # the held call is judged again under its own id
        q.pending_call_id = hold.call_id
        if pending == "TOOL_CALL":
            q.hitl_tools_approved_turn = True
        gov_clear_hitl(q, hold.request_id)
        gov_on_hitl_schedule(q)
        return emit_scheduled_egress(q, run, config)

    if resolution == HitlResolveChoice.TERMINATE:
        q.ctrl = LifecycleState.STOPPED
        q.dp = DpState.IDLE
        q.ctx = CtxState.IDLE
        q.scheduled_egress = "NONE"
        q.inflight_kind = "NONE"
        q.model = ModelState.IDLE
        q.tool = tool_on_abort(q.tool)
        q.memory = MemoryState.IDLE
        q.transport = TransportState.IDLE
        q.session = SessionState.IDLE
        gov_clear_hitl(q)
        return [NoOp()]

    if resolution in {HitlResolveChoice.BLOCK, HitlResolveChoice.SKIP}:
        q.scheduled_egress = "NONE"
        q.inflight_kind = "NONE"
        q.tool = tool_on_abort(q.tool)
        gov_clear_hitl(q, hold.request_id)
        blocked = resolution == HitlResolveChoice.BLOCK
        result_msg = _operator_tool_text(
            blocked=blocked,
            tool_ran=False,
            tool_name=hold.tool_name or "tool",
            steering=event.steering_text,
        )
        if blocked:
            q.hitl_block_committed = True
            run.append_placeholder(
                cid=hold.request_id,
                text=result_msg,
                next_step="STOP",
                tool_name=hold.tool_name,
                tool_arguments=hold.tool_arguments,
                call_id=hold.call_id,
            )
            return _end_turn_with_denial(q, run, result_msg)
        q.hitl_skip_committed = True
        run.append_placeholder(
            cid=hold.request_id,
            text=result_msg,
            next_step="LLM_CALL",
            tool_name=hold.tool_name,
            tool_arguments=hold.tool_arguments,
            call_id=hold.call_id,
        )
        if q.queued_tools or q.inflight_correlation_ids:
            # The rest of the batch is still to run or to answer: the model is asked after the last result.
            q.dp = DpState.AWAITING_INGRESS
            if q.queued_tools:
                from mas.runtime.kernel.parallel_tools import advance_tool_queue

                queued = advance_tool_queue(q, run, config)
                if queued is not None:
                    return queued
            return [NoOp()]
        q.scheduled_egress = "LLM_CALL"
        q.dp = DpState.EGRESS_PENDING
        return emit_scheduled_egress(q, run, config)

    return [NoOp()]
