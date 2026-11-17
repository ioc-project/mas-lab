#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""TransitionEvent — normalized observability unit for plugin export."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from mas.runtime.schema.observability import ObservabilityEvent, ObsEventKind

_CONTRACT_BY_MACHINE: dict[str, str] = {
    "M_model": "model",
    "M_tool": "tool",
    "M_memory": "memory",
    "M_ctx": "context",
    "M_gov": "governance",
    "M_obs": "observability",
    "M_dp": "orchestrator",
    "M_control": "control",
    "execution_engine": "orchestrator",
}


@dataclass(frozen=True)
class TransitionEvent:
    """One observable Mealy step at a contract boundary (read-only telemetry)."""

    contract_id: str
    mealy_symbol: str
    phase: str
    agent_id: str = "agent"
    run_id: str = ""
    # session_id: one id for the whole MAS run, shared by every agent in it.
    # task_id: one id per input prompt, local to this agent. Same values and
    # same propagation rules as mas.runtime.boundary.gov.transition.GovTransition
    # — set from mas.runtime.driver.driver.KernelDriver.session_id /
    # _current_task_id, which is where every consumer should look to
    # understand how these are minted/propagated.
    session_id: str = ""
    task_id: str = ""
    correlation_id: int = 0
    upstream_correlation_id: int | None = None
    call_id: str | None = None
    parent_call_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    boundary_kind: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "contract_id": self.contract_id,
            "mealy_symbol": self.mealy_symbol,
            "phase": self.phase,
            "agent_id": self.agent_id,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "correlation_id": self.correlation_id,
            "timestamp": self.timestamp,
            "boundary_kind": self.boundary_kind,
            **self.attributes,
        }
        if self.upstream_correlation_id is not None:
            out["upstream_correlation_id"] = self.upstream_correlation_id
        if self.call_id is not None:
            out["call_id"] = self.call_id
        if self.parent_call_id is not None:
            out["parent_call_id"] = self.parent_call_id
        return out


# Kernel records emitted while a call runs. They are owned by that call (their
# parent) and have no call id of their own.
ENCLOSED_KINDS = frozenset(
    {
        ObsEventKind.BOUNDARY_INGRESS,
        ObsEventKind.BOUNDARY_EGRESS,
        ObsEventKind.CLIENT_RESPONSE,
        ObsEventKind.CONTEXT_STEER,
    }
)


def _contract_id(event: ObservabilityEvent) -> str:
    mid = event.machine_id or ""
    if mid in _CONTRACT_BY_MACHINE:
        return _CONTRACT_BY_MACHINE[mid]
    if event.kind in (ObsEventKind.HITL_REQUEST, ObsEventKind.HITL_RESOLVE, ObsEventKind.GOVERNANCE_DECISION):
        return "governance"
    if event.kind in (ObsEventKind.CONTEXT_ASSEMBLED, ObsEventKind.CONTEXT_MUTATION, ObsEventKind.CONTEXT_STEER):
        return "context"
    if event.kind == ObsEventKind.CONTROL:
        return "control"
    if event.kind in (ObsEventKind.ENGINE_IO, ObsEventKind.ENGINE_IO_RETURN):
        op = (event.payload or {}).get("op", "LLM_CALL")
        if op == "TOOL_CALL":
            return "tool"
        if op == "MEMORY_OP":
            return "memory"
        return "model"
    if event.kind == ObsEventKind.ENVELOPE_ACTIVITY:
        activity = (event.payload or {}).get("activity", "")
        if activity in ("gov_authorize", "gov_validate", "governance_authorize", "governance_validate"):
            return "governance"
        if activity == "parallel_group":
            return "orchestrator"
        if activity == "contract_call":
            op = (event.payload or {}).get("op", "LLM_CALL")
            if op == "TOOL_CALL":
                return "tool"
            if op == "MEMORY_OP":
                return "memory"
            return "model"
        if activity.startswith("obs_wrap"):
            return "observability"
        return "orchestrator"
    return "orchestrator"


def _mealy_symbol(event: ObservabilityEvent) -> str:
    payload = event.payload or {}
    if event.kind == ObsEventKind.ENVELOPE_ACTIVITY:
        return str(payload.get("symbol") or payload.get("activity") or event.kind.value)
    if event.kind in (ObsEventKind.ENGINE_IO, ObsEventKind.ENGINE_IO_RETURN):
        return str(payload.get("op") or event.kind.value)
    return event.kind.value


def _phase(event: ObservabilityEvent) -> str:
    payload = event.payload or {}
    if event.kind == ObsEventKind.ENVELOPE_ACTIVITY:
        boundary = payload.get("boundary", "event")
        if boundary in ("start", "end"):
            return boundary
        return "event"
    if event.kind == ObsEventKind.LLM_THINKING:
        return str(payload.get("boundary") or "event")
    if event.kind == ObsEventKind.ENGINE_IO:
        return "start"
    if event.kind == ObsEventKind.ENGINE_IO_RETURN:
        return "end"
    if event.kind in (ObsEventKind.CONTEXT_ASSEMBLED, ObsEventKind.CONTEXT_MUTATION):
        return "event"
    return "event"


def boundary_event_to_transition(
    event: ObservabilityEvent,
    *,
    agent_id: str = "agent",
    run_id: str = "",
    session_id: str = "",
    task_id: str = "",
    timestamp: float | None = None,
    call_id: str | None = None,
    parent_call_id: str | None = None,
    upstream_correlation_id: int | None = None,
) -> TransitionEvent:
    """Map v2 ObservabilityEvent → plugin-facing TransitionEvent."""
    payload = dict(event.payload or {})
    cid = event.correlation_id
    op = payload.get("op", "")
    resolved_call_id = call_id
    if resolved_call_id is None and event.kind not in ENCLOSED_KINDS:
        if cid and op:
            if op == "TOOL_CALL":
                prefix = "tool"
            elif op == "MEMORY_OP":
                prefix = "memory"
            else:
                prefix = "llm"
            resolved_call_id = f"{prefix}-{cid}"
        elif cid:
            resolved_call_id = f"call-{cid}"

    return TransitionEvent(
        contract_id=_contract_id(event),
        mealy_symbol=_mealy_symbol(event),
        phase=_phase(event),
        agent_id=agent_id,
        run_id=run_id,
        session_id=session_id,
        task_id=task_id,
        correlation_id=cid,
        upstream_correlation_id=upstream_correlation_id,
        call_id=resolved_call_id,
        parent_call_id=parent_call_id,
        attributes=payload,
        timestamp=timestamp if timestamp is not None else time.time(),
        boundary_kind=event.kind.value if isinstance(event.kind, ObsEventKind) else str(event.kind),
    )
