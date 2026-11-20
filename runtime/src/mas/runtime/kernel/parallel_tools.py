#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Parallel tool egress — multiple TOOL_CALL intents in one agentic step."""

from __future__ import annotations

from mas.runtime.boundary.gov.telemetry import get_bound_observability
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.coupling import apply_control_tool_request, apply_coupling_patch, coupling_for_gov_hitl_hold
from mas.runtime.kernel.egress_gate import (
    AuthorizedEgress,
    abandon_egress,
    authorize_scheduled_egress,
    commit_egress,
    emit_scheduled_egress,
    schedule_tool_egress,
)
from mas.runtime.kernel.inflight import register_inflight
from mas.runtime.kernel.state import DpState, QProduct, RunLedger
from mas.runtime.schema.egress import EgressSymbol, EmitHitlRequest, InvokeEngineIo, NoOp, RaiseBoundaryError
from mas.runtime.schema.ingress import ToolCallSpec


def _declared_ids(run: RunLedger, count: int) -> list[str]:
    """The ids the transcript gives the calls of the model reply being scheduled (``call_<reply id>_<index>``)."""
    reply = max((r for r in run.events if r.parallel_tools), key=lambda r: r.seq, default=None)
    base = reply.correlation_id if reply is not None else 0
    return [f"call_{base}_{i}" for i in range(count)]


def schedule_parallel_tools_egress(
    q: QProduct,
    run: RunLedger,
    config: KernelConfig,
    tools: list[ToolCallSpec],
) -> list[EgressSymbol]:
    """Run gov/chokepoint per tool; emit allowed InvokeEngineIo, feed denials back.

    Each TOOL_CALL is authorized on its own. A BLOCK records that call's
    denial as a tool observation and the rest of the batch still runs. HITL
    and turn-ending errors (TERMINATE, plugin failure) still stop the batch.
    """
    if not tools:
        return schedule_tool_egress(q, run, config)
    if len(tools) == 1:
        first = tools[0]
        q.pending_tool_name = first.tool_name
        q.pending_tool_args = dict(first.tool_arguments)
        return schedule_tool_egress(q, run, config)
    if not config.parallel_tool_calls:
        # One at a time, but all of them, in this reply's order. The model is
        # asked once after the last result, not once per call.
        ids = _declared_ids(run, len(tools))
        q.queued_tools = [
            {"tool_name": t.tool_name, "tool_arguments": dict(t.tool_arguments), "call_id": ids[i]}
            for i, t in enumerate(tools)
        ]
        q.pending_tools_by_cid.clear()
        q.call_slots.clear()
        return advance_tool_queue(q, run, config) or _ask_model_after_tools(q, run, config)

    q.pending_tools_by_cid.clear()
    q.call_slots.clear()
    ids = _declared_ids(run, len(tools))
    # Snapshot the enclosing frame ONCE, before any of these N tools' own
    # contract_call/start fires below (each iteration's authorization steps
    # CONTRACT_START synchronously) — otherwise each sibling reads
    # whatever the PREVIOUS sibling just pushed as its parent, chaining them
    # onto each other instead of the frame that's actually enclosing all of
    # them. See ObservabilityOperator.begin_sibling_batch.
    obs = get_bound_observability()
    if obs is not None:
        obs.begin_sibling_batch(len(tools))
    # Two phases, so the batch commits as a unit. Phase 1 decides every call
    # and registers nothing outbound. Phase 2 registers and emits the allowed
    # ones. An abort in phase 1 (HITL, TERMINATE, plugin error) abandons the
    # calls already authorized: no wait is left that nothing will answer, and
    # no envelope is left open. A hold is not an abort: see below.
    authorized: list[tuple[ToolCallSpec, AuthorizedEgress]] = []
    for index, spec in enumerate(tools):
        q.pending_tool_name = spec.tool_name
        q.pending_tool_args = dict(spec.tool_arguments)
        q.pending_call_id = ids[index]
        apply_control_tool_request(q)
        outcome = authorize_scheduled_egress(q, run, config)
        if isinstance(outcome, AuthorizedEgress):
            authorized.append((spec, outcome))
            continue
        if any(isinstance(sym, RaiseBoundaryError) for sym in outcome):
            for _spec, pending in authorized:
                abandon_egress(pending)
            return outcome
        if any(isinstance(sym, EmitHitlRequest) for sym in outcome):
            # A hold stops the batch at its call, not the calls already judged: those run now, the calls
            # after it wait in the queue and are judged one at a time once the held call has run.
            started = _commit_authorized(q, authorized)
            q.queued_tools = [
                {"tool_name": t.tool_name, "tool_arguments": dict(t.tool_arguments), "call_id": ids[index + 1 + i]}
                for i, t in enumerate(tools[index + 1 :])
            ]
            apply_coupling_patch(q, coupling_for_gov_hitl_hold(op="TOOL_CALL"))
            return [*started, *outcome]
    started = _commit_authorized(q, authorized)
    if started:
        return started
    # Every call was denied. Ask the model once, as after a single denied tool.
    return _ask_model_after_tools(q, run, config)


def _commit_authorized(q: QProduct, authorized: list[tuple[ToolCallSpec, AuthorizedEgress]]) -> list[EgressSymbol]:
    """Phase 2: register and emit the calls that passed the chokepoint."""
    out: list[EgressSymbol] = []
    for spec, pending in authorized:
        for sym in commit_egress(q, pending):
            if isinstance(sym, InvokeEngineIo) and sym.op == "TOOL_CALL":
                register_inflight(q, sym.correlation_id, kind="TOOL", op="TOOL_CALL")
                q.pending_tools_by_cid[sym.correlation_id] = (
                    spec.tool_name,
                    dict(spec.tool_arguments),
                )
                out.append(sym)
    if out:
        q.scheduled_egress = "NONE"
        q.dp = DpState.AWAITING_INGRESS
        q.inflight_kind = "TOOL"
    return out


def _ask_model_after_tools(q: QProduct, run: RunLedger, config: KernelConfig) -> list[EgressSymbol]:
    q.pending_tool_name = ""
    q.pending_tool_args = {}
    q.scheduled_egress = "LLM_CALL"
    q.dp = DpState.EGRESS_PENDING
    return emit_scheduled_egress(q, run, config)


def advance_tool_queue(q: QProduct, run: RunLedger, config: KernelConfig) -> list[EgressSymbol] | None:
    """Start the next queued call (parallel off). ``None`` when none is left to run.

    A denied call is that call's result and the loop moves on. A HITL pause or
    a turn-ending error is returned as is; the rest stay queued and resume
    after the paused call's result.
    """
    while q.queued_tools:
        nxt = q.queued_tools.pop(0)
        q.pending_tool_name = str(nxt["tool_name"])
        q.pending_tool_args = dict(nxt.get("tool_arguments") or {})
        q.pending_call_id = str(nxt.get("call_id") or "")
        out = schedule_tool_egress(q, run, config)
        if any(not isinstance(sym, NoOp) for sym in out):
            return out
    return None
