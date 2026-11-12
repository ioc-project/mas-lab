#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Serialize / restore Q_product and run ledger for session checkpoints."""

from __future__ import annotations

from mas.runtime.kernel.state import (
    CtxState,
    DpState,
    LifecycleState,
    MemoryState,
    ModelState,
    QProduct,
    RunEvent,
    RunLedger,
    SessionState,
    ToolState,
    TransportState,
)
from mas.runtime.kernel.types import OutboundWait


def q_product_to_dict(q: QProduct) -> dict:
    return {
        "ctrl": q.ctrl.value,
        "dp": q.dp.value,
        "model": q.model.value,
        "tool": q.tool.value,
        "ctx": q.ctx.value,
        "memory": q.memory.value,
        "session": q.session.value,
        "transport": q.transport.value,
        "scheduled_egress": q.scheduled_egress,
        "inflight_kind": q.inflight_kind,
        "hitl_request_id": q.hitl_request_id,
        "hitl_pending_schedule": q.hitl_pending_schedule,
        "hitl_question_type": q.hitl_question_type,
        "hitl_gov_override": q.hitl_gov_override,
        "cot_pass": q.cot_pass,
        "gov_retry_count": q.gov_retry_count,
        "tool_blacklisted": q.tool_blacklisted,
        "gov_state": q.gov_state,
        "obs_state": q.obs_state,
        "hitl_skip_committed": q.hitl_skip_committed,
        "hitl_block_committed": q.hitl_block_committed,
        "hitl_tools_approved_turn": q.hitl_tools_approved_turn,
        "control_phase": q.control_phase,
        "pending_engine_correlation_id": q.pending_engine_correlation_id,
        "outbound_waits": [{"correlation_id": w.correlation_id, "kind": w.kind, "op": w.op} for w in q.outbound_waits],
        "inflight_correlation_ids": list(q.inflight_correlation_ids),
        "offered_tools": list(q.offered_tools) if q.offered_tools is not None else None,
        "pending_tool_name": q.pending_tool_name,
        "pending_tool_args": dict(q.pending_tool_args),
        "pending_tools_by_cid": {
            str(k): {"tool_name": v[0], "tool_arguments": dict(v[1])} for k, v in q.pending_tools_by_cid.items()
        },
        "parallel_tool_batch": list(q.parallel_tool_batch),
        "queued_tools": list(q.queued_tools),
    }


def q_product_from_dict(data: dict) -> QProduct:
    dp_raw = data["dp"]
    tool_raw = data.get("tool", "IDLE")
    gov_raw = data.get("gov_state", "IDLE")
    # Legacy checkpoints: HITL lived on M_dp; migrate to gov/tool chain.
    if dp_raw == "AWAITING_HITL":
        dp_raw = "AWAITING_INGRESS"
        tool_raw = "WAIT_GOV"
        if gov_raw == "IDLE" and int(data.get("hitl_request_id", 0)) > 0:
            gov_raw = "HITL_PENDING"
    return QProduct(
        ctrl=LifecycleState(data["ctrl"]),
        dp=DpState(dp_raw),
        model=ModelState(data["model"]),
        tool=ToolState(tool_raw),
        ctx=CtxState(data["ctx"]),
        memory=MemoryState(data["memory"]),
        session=SessionState(data["session"]),
        transport=TransportState(data["transport"]),
        scheduled_egress=data.get("scheduled_egress", "NONE"),
        inflight_kind=data.get("inflight_kind", "NONE"),
        hitl_request_id=int(data.get("hitl_request_id", 0)),
        hitl_pending_schedule=data.get("hitl_pending_schedule", "NONE"),
        hitl_question_type=data.get("hitl_question_type", ""),
        hitl_gov_override=bool(data.get("hitl_gov_override", False)),
        cot_pass=int(data.get("cot_pass", 0)),
        gov_retry_count=int(data.get("gov_retry_count", 0)),
        tool_blacklisted=bool(data.get("tool_blacklisted", False)),
        gov_state=gov_raw,
        obs_state=data.get("obs_state", "IDLE"),
        hitl_skip_committed=bool(data.get("hitl_skip_committed", False)),
        hitl_block_committed=bool(data.get("hitl_block_committed", False)),
        hitl_tools_approved_turn=bool(data.get("hitl_tools_approved_turn", False)),
        control_phase=data.get("control_phase", "IDLE"),
        pending_engine_correlation_id=int(data.get("pending_engine_correlation_id", 0)),
        outbound_waits=[
            OutboundWait(
                correlation_id=int(row["correlation_id"]),
                kind=row.get("kind", "TOOL"),
                op=row.get("op", "NONE"),
            )
            for row in (data.get("outbound_waits") or [])
        ],
        inflight_correlation_ids=[int(x) for x in (data.get("inflight_correlation_ids") or [])],
        offered_tools=(tuple(data["offered_tools"]) if data.get("offered_tools") is not None else None),
        pending_tool_name=data.get("pending_tool_name", ""),
        pending_tool_args=dict(data.get("pending_tool_args") or {}),
        pending_tools_by_cid={
            int(k): (
                str((v or {}).get("tool_name", "")),
                dict((v or {}).get("tool_arguments") or {}),
            )
            for k, v in (data.get("pending_tools_by_cid") or {}).items()
        },
        parallel_tool_batch=list(data.get("parallel_tool_batch") or []),
        queued_tools=list(data.get("queued_tools") or []),
    )


def run_to_dict(run: RunLedger) -> dict:
    return {
        "events": [
            {
                "correlation_id": r.correlation_id,
                "response_kind": r.response_kind,
                "next_step": r.next_step,
                "text": r.text,
                "tool_name": r.tool_name,
                "tool_arguments": dict(r.tool_arguments or {}),
                "parallel_tools": [
                    {"tool_name": name, "tool_arguments": dict(args or {})} for name, args in (r.parallel_tools or ())
                ],
                "finish_reason": r.finish_reason,
                **({"placeholder": True} if r.placeholder else {}),
            }
            for r in run.events
        ]
    }


def run_from_dict(data: dict) -> RunLedger:
    ledger = RunLedger()
    rows = data.get("events") or data.get("records") or []
    for row in rows:
        parallel = tuple(
            (str(item.get("tool_name") or "tool"), dict(item.get("tool_arguments") or {}))
            for item in (row.get("parallel_tools") or [])
            if isinstance(item, dict)
        )
        ledger.append(
            RunEvent(
                correlation_id=int(row["correlation_id"]),
                response_kind=row["response_kind"],
                next_step=row["next_step"],
                text=str(row.get("text") or ""),
                tool_name=str(row.get("tool_name") or ""),
                tool_arguments=dict(row.get("tool_arguments") or {}),
                parallel_tools=parallel,
                finish_reason=str(row.get("finish_reason") or ""),
                placeholder=bool(row.get("placeholder")),
            )
        )
    return ledger
