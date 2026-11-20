#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Serialize / restore Q_product and run ledger for session checkpoints."""

from __future__ import annotations

import copy
from dataclasses import asdict, fields
from enum import Enum

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
from mas.runtime.kernel.types import HitlHold, OutboundWait

# Not part of a checkpoint: the session keeps the running spec and hands it to the kernel on every step.
_NOT_CHECKPOINTED = frozenset({"running_spec"})


def _encode(name: str, value):
    if isinstance(value, Enum):
        return value.value
    if name == "outbound_waits":
        return [{"correlation_id": w.correlation_id, "kind": w.kind, "op": w.op} for w in value]
    if name == "hitl_holds":
        return {str(rid): asdict(hold) for rid, hold in value.items()}
    if name == "pending_tools_by_cid":
        return {str(k): {"tool_name": v[0], "tool_arguments": dict(v[1])} for k, v in value.items()}
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, (dict, list)):
        return copy.deepcopy(value)
    return value


def q_product_to_dict(q: QProduct) -> dict:
    """Every field of the product state, so a restore cannot lose one that a later turn depends on."""
    return {f.name: _encode(f.name, getattr(q, f.name)) for f in fields(QProduct) if f.name not in _NOT_CHECKPOINTED}


_ENUMS = {
    "ctrl": LifecycleState,
    "dp": DpState,
    "model": ModelState,
    "tool": ToolState,
    "ctx": CtxState,
    "memory": MemoryState,
    "session": SessionState,
    "transport": TransportState,
}


def _decode(name: str, raw):
    if name in _ENUMS:
        return _ENUMS[name](raw)
    if name == "outbound_waits":
        return [
            OutboundWait(
                correlation_id=int(row["correlation_id"]),
                kind=row.get("kind", "TOOL"),
                op=row.get("op", "NONE"),
            )
            for row in raw
        ]
    if name == "hitl_holds":
        known = {f.name for f in fields(HitlHold)}
        for row in raw.values():
            if unknown := sorted(set(row) - known):
                raise CheckpointVersionError(
                    f"a hold in this checkpoint has fields this version does not know: {unknown}"
                )
        return {int(rid): HitlHold(**row) for rid, row in raw.items()}
    if name == "call_slots":
        return {int(k): str(v) for k, v in raw.items()}
    if name == "pending_tools_by_cid":
        return {
            int(k): (str((v or {}).get("tool_name", "")), dict((v or {}).get("tool_arguments") or {}))
            for k, v in raw.items()
        }
    if name == "offered_tools":
        return tuple(raw) if raw is not None else None
    if name == "inflight_correlation_ids":
        return [int(x) for x in raw]
    return copy.deepcopy(raw)


class CheckpointVersionError(ValueError):
    """A checkpoint written by a version that has fields this one does not know. Loading it would drop them."""


def _migrate_legacy(data: dict) -> dict:
    """Bring a checkpoint written before holds were a collection to the current shape, or refuse it.

    Retired fields and what became of them:

    - ``hitl_request_id``, ``hitl_pending_schedule``, ``hitl_question_type``, ``hitl_pending_ingress``,
      ``pending_ingress_return``: the one hold slot. A pending question is rebuilt as one hold under the same
      request id, so an answer addressed to it still resolves it. A result held for a human answer is
      rebuilt only if the checkpoint kept the result; the first versions did not write it, and without it the
      hold cannot be answered, so such a checkpoint is refused.
    - ``tool_blacklisted``: a session-wide flag that skipped every tool. It cannot be mapped to the per-tool
      ``tool_blacklist`` (the tool is unknown), and dropping it would run tools the session had blacklisted,
      so a checkpoint with it set is refused. When it is false it is dropped.
    """
    data = dict(data)
    if data.pop("tool_blacklisted", False):
        raise CheckpointVersionError(
            "this checkpoint has a session-wide tool blacklist, which this version replaces by a per-tool one; "
            "it cannot say which tool was blacklisted, so it is not loaded"
        )
    request_id = int(data.pop("hitl_request_id", 0) or 0)
    schedule = data.pop("hitl_pending_schedule", "NONE")
    question_type = data.pop("hitl_question_type", "") or "CONFIRM"
    data.pop("hitl_pending_ingress", None)
    parked = data.pop("pending_ingress_return", None) or {}
    if request_id > 0 and "hitl_holds" not in data:
        hold: dict = {"request_id": request_id, "question_type": question_type}
        if parked:
            hold.update(pending_schedule="NONE", tool_name=data.get("pending_tool_name", ""), ingress_return=parked)
        elif schedule != "NONE":
            hold.update(
                pending_schedule=schedule,
                tool_name=data.get("pending_tool_name", ""),
                tool_arguments=dict(data.get("pending_tool_args") or {}),
            )
        else:
            raise CheckpointVersionError(
                "this checkpoint was taken while a tool result waited for a human answer, and that version did not "
                "save the result, so the question cannot be answered; it is not loaded"
            )
        data["hitl_holds"] = {str(request_id): hold}
    return data


def q_product_from_dict(data: dict) -> QProduct:
    data = _migrate_legacy(data)
    if unknown := sorted(set(data) - {f.name for f in fields(QProduct)}):
        raise CheckpointVersionError(f"this checkpoint has fields this version does not know: {unknown}")
    # Legacy checkpoints: HITL lived on M_dp; migrate to gov/tool chain.
    if data["dp"] == "AWAITING_HITL":
        data["dp"] = "AWAITING_INGRESS"
        data["tool"] = "WAIT_GOV"
    values = {}
    for f in fields(QProduct):
        if f.name in _NOT_CHECKPOINTED or f.name not in data:
            continue  # a checkpoint older than the field: the field keeps its default
        values[f.name] = _decode(f.name, data[f.name])
    return QProduct(**values)


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
                **({"provider_state": dict(r.provider_state)} if r.provider_state else {}),
                **({"placeholder": True} if r.placeholder else {}),
                **({"call_id": r.call_id} if r.call_id else {}),
                "seq": r.seq,
            }
            for r in run.events
        ],
        "allocated_high": run.allocated_high,
        "next_seq": run.max_seq(),
    }


def run_from_dict(data: dict) -> RunLedger:
    rows = data.get("events") or data.get("records") or []
    events = []
    for row in rows:
        parallel = tuple(
            (str(item.get("tool_name") or "tool"), dict(item.get("tool_arguments") or {}))
            for item in (row.get("parallel_tools") or [])
            if isinstance(item, dict)
        )
        events.append(
            RunEvent(
                correlation_id=int(row["correlation_id"]),
                response_kind=row["response_kind"],
                next_step=row["next_step"],
                text=str(row.get("text") or ""),
                tool_name=str(row.get("tool_name") or ""),
                tool_arguments=dict(row.get("tool_arguments") or {}),
                parallel_tools=parallel,
                finish_reason=str(row.get("finish_reason") or ""),
                provider_state=dict(row.get("provider_state") or {}),
                placeholder=bool(row.get("placeholder")),
                call_id=str(row.get("call_id") or ""),
                seq=int(row.get("seq") or 0),
            )
        )
    return RunLedger.restore(
        events,
        allocated_high=int(data.get("allocated_high") or 0),
        next_seq=int(data.get("next_seq") or 0),
    )
