#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""GovTransition — everything a governance plugin needs, for every Mealy step.

Mirrors ``mas.runtime.boundary.obs.transition``'s role for observability
(one normalized, read-only unit per boundary step), but carries what a
governance *decision* needs rather than what a telemetry *export* needs:
the raw symbol (nothing redacted), the live product-automaton state, and
identifiers a decision needs to correlate against (session, task, agent) —
not just enough to render a trace.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from mas.runtime.schema.ingress import HitlResolve

if TYPE_CHECKING:
    from mas.runtime.kernel.state import QProduct
    from mas.runtime.schema.egress import EgressSymbol
    from mas.runtime.schema.ingress import IngressSymbol

GovHook = Literal["ingress", "egress"]


@dataclass(frozen=True)
class GovTransition:
    """One ingress or egress symbol, with everything relevant to governance.

    hook
        "egress" — the driver is about to send *symbol* out (e.g. a tool
        call about to execute, an LLM call about to be invoked). "ingress"
        — *symbol* is something arriving back in (a user prompt, an engine
        return, a HITL resolution).  "before a tool call" is
        hook="egress", op="TOOL_CALL"; "after a tool call" is
        hook="ingress", response_kind="TOOL_RESULT".
    kind
        The symbol's own discriminator (``symbol.kind.value`` — e.g.
        "USER_INPUT_RECEIVED", "INVOKE_ENGINE_IO", "ENGINE_IO_RETURN").
    op / response_kind
        Only populated where the symbol carries them: ``op`` for an
        egress ``InvokeEngineIo`` ("TOOL_CALL"/"LLM_CALL"/"MEMORY_OP"/
        "TRANSPORT_MSG"); ``response_kind`` for an ingress
        ``EngineIoReturn`` ("TOOL_RESULT"/"MODEL_TEXT"/"ERROR"). Empty
        string when not applicable to this symbol.
    machine
        Owning Mealy machine for this step (``M_tool``, ``M_model``,
        ``M_memory``, ``M_gov``, ``execution_engine``, …) — same
        vocabulary as ``GovTransitionFilter.machine``.
    session_id
        One id for the whole run (shared across every agent in a
        multi-agent MAS when observability wires it — see
        ``KernelDriver.session_id``; otherwise a fresh id per driver).
    task_id
        One id per input prompt — every transition produced while the
        driver is processing one ``UserInputReceived`` (that turn's LLM
        calls, tool calls, tool results, …) shares this same value, up to
        the next ``UserInputReceived``. See
        ``mas.runtime.schema.ingress.UserInputReceived.task_id``.
    agent_spec
        ``manifest["spec"]`` for the agent this transition belongs to —
        the agent's own declared tools/context/models, for a plugin that
        needs to reason about what the agent is *supposed* to be able to
        do, not just what's happening live. None if unavailable (e.g. a
        KernelConfig built without going through spec parsing).
    q_state
        Snapshot of the live product-automaton state at this step —
        same shape as ``ObservabilityOperator.record_ingress``'s
        ``product`` payload (``dp``, ``ctx``, ``model``, ``tool``,
        ``gov``, ``scheduled_egress``).
    attributes
        Every field the symbol itself carries (text, destructive, …), as a
        plain dict — nothing redacted, unlike observability's
        ``TransitionEvent.attributes``. For an egress TOOL_CALL specifically,
        also includes ``tool_name``/``tool_arguments`` resolved from kernel
        state — ``InvokeEngineIo`` itself doesn't carry them.
    """

    hook: GovHook
    symbol: "IngressSymbol | EgressSymbol"
    kind: str
    op: str
    response_kind: str
    machine: str
    correlation_id: int
    destructive: bool
    agent_id: str
    session_id: str
    task_id: str
    agent_spec: dict[str, Any] | None
    q_state: dict[str, Any]
    attributes: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


def _machine_for(hook: GovHook, symbol: Any) -> str:
    if hook == "egress":
        op = getattr(symbol, "op", "")
        if op == "TOOL_CALL":
            return "M_tool"
        if op == "MEMORY_OP":
            return "M_memory"
        if op == "LLM_CALL":
            return "M_model"
        return "M_dp"
    if isinstance(symbol, HitlResolve):
        return "M_gov"
    response_kind = getattr(symbol, "response_kind", "")
    if response_kind == "TOOL_RESULT":
        return "M_tool"
    if response_kind in ("MODEL_TEXT", "ERROR"):
        return "M_model"
    return "execution_engine"


def _q_state(q: "QProduct | None") -> dict[str, Any]:
    if q is None:
        return {}
    return {
        "dp": q.dp.value,
        "ctx": q.ctx.value,
        "model": q.model.value,
        "tool": q.tool.value,
        "gov": q.gov_state,
        "scheduled_egress": q.scheduled_egress,
    }


def _tool_call_attributes(symbol: Any, q: "QProduct | None") -> dict[str, Any]:
    """Resolve tool_name/tool_arguments for an egress TOOL_CALL InvokeEngineIo.

    InvokeEngineIo itself doesn't carry them — the driver resolves them from
    kernel state the same way at dispatch time (see driver.py's
    _dispatch_engine_batch: pending_tools_by_cid keyed by correlation_id,
    falling back to the single shared pending_tool_name/args for the
    non-parallel case). Governance sees this at the same point in the loop,
    before that dispatch, so the same resolution applies here.
    """
    if q is None or getattr(symbol, "op", "") != "TOOL_CALL":
        return {}
    correlation_id = getattr(symbol, "correlation_id", 0)
    by_cid = q.pending_tools_by_cid.get(correlation_id)
    name, args = by_cid if by_cid is not None else (q.pending_tool_name, q.pending_tool_args)
    if not name:
        return {}
    return {"tool_name": name, "tool_arguments": dict(args or {})}


def build_gov_transition(
    hook: GovHook,
    symbol: "IngressSymbol | EgressSymbol",
    *,
    q: "QProduct | None" = None,
    agent_id: str = "agent",
    session_id: str = "",
    task_id: str = "",
    agent_spec: dict[str, Any] | None = None,
) -> GovTransition:
    """Build a :class:`GovTransition` from one raw driver symbol."""
    attributes: dict[str, Any] = (
        symbol.model_dump(mode="json", exclude={"provider_state"}) if hasattr(symbol, "model_dump") else {}
    )
    if hook == "egress":
        attributes.update(_tool_call_attributes(symbol, q))
    return GovTransition(
        hook=hook,
        symbol=symbol,
        kind=symbol.kind.value,
        op=getattr(symbol, "op", ""),
        response_kind=getattr(symbol, "response_kind", ""),
        machine=_machine_for(hook, symbol),
        correlation_id=getattr(symbol, "correlation_id", 0),
        destructive=bool(getattr(symbol, "destructive", False)),
        agent_id=agent_id,
        session_id=session_id,
        task_id=task_id,
        agent_spec=agent_spec,
        q_state=_q_state(q),
        attributes=attributes,
    )
