#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Base class for deterministic design-pattern plugins."""

from __future__ import annotations

from dataclasses import dataclass, field

from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.ingress_step import apply_engine_io_return
from mas.runtime.kernel.state import DpState, QProduct, RunLedger
from mas.runtime.machines.context import ctx_on_assembly_complete, ctx_on_user_input
from mas.runtime.machines.design_pattern.protocol import DesignPatternPlugin
from mas.runtime.schema.egress import EgressSymbol, EmitClientResponse, NoOp, RequestCtxAssembly
from mas.runtime.schema.ingress import (
    CtxAssemblyComplete,
    EngineIoReturn,
    IngressSymbol,
    UserInputReceived,
)


@dataclass
class _TurnState:
    participants: list[str] = field(default_factory=list)
    original_task: str = ""
    next_idx: int = 0
    processed_tool_results: int = 0
    round_num: int = 1


class _DeterministicBase(DesignPatternPlugin):
    """Base class for all deterministic (non-LLM-driven) collaboration patterns."""

    plugin_id = "deterministic_base@v1"
    mode: str = "single"
    STEER_MODES = frozenset({"after"})

    def __init__(self) -> None:
        self._turn_state: _TurnState = self._make_state()

    # --- State management ---

    def _make_state(self) -> _TurnState:
        """Factory for per-turn state. Override in subclasses that need extra fields."""
        return _TurnState()

    def _state(self) -> _TurnState:
        return self._turn_state

    def _reset_state(self, participants: list[str], task: str) -> _TurnState:
        """Replace current turn state with a fresh one, populated with turn context."""
        self._turn_state = self._make_state()
        self._turn_state.participants = participants
        self._turn_state.original_task = task
        return self._turn_state

    # --- Shared helpers ---

    @staticmethod
    def _delegate_tool_name(agent_id: str) -> str:
        return f"delegate_to_{agent_id}"

    @staticmethod
    def _extract_tool_results(run: RunLedger) -> list[str]:
        return [e.text.strip() for e in run.events if e.response_kind == "TOOL_RESULT" and e.text.strip()]

    def _no_participants(self, q: QProduct) -> list[EgressSymbol]:
        """Return an early-exit response when no participants are configured."""
        q.dp = DpState.IDLE
        q.scheduled_egress = "NONE"
        return [EmitClientResponse(content="Deterministic run complete (no participants).", finish_reason="stop")]

    def _finish(self, q: QProduct, txt: str) -> list[EgressSymbol]:
        """Mark the turn complete and emit the final response to the client."""
        q.dp = DpState.IDLE
        q.scheduled_egress = "NONE"
        return [EmitClientResponse(content=txt, finish_reason="stop")]

    # --- Protocol implementation (identical for all deterministic patterns) ---

    def handle_event(
        self,
        q: QProduct,
        run: RunLedger,
        event: IngressSymbol,
        *,
        config: KernelConfig,
    ) -> list[EgressSymbol]:
        if isinstance(event, UserInputReceived) and q.dp == DpState.IDLE:
            self._reset_state(
                participants=self._participants_from_spec(config),
                task=str(event.text or ""),
            )
            q.dp = DpState.CTX_BUILD
            q.ctx = ctx_on_user_input(q.ctx)
            return [RequestCtxAssembly(collect_id=event.user_turn_id)]

        if isinstance(event, CtxAssemblyComplete) and q.dp == DpState.CTX_BUILD:
            q.ctx = ctx_on_assembly_complete(q.ctx)
            q.dp = DpState.EVALUATING
            return self.evaluate_next(q, run, config=config)

        if isinstance(event, EngineIoReturn) and q.dp == DpState.AWAITING_INGRESS:
            return apply_engine_io_return(q, run, event, config=config, evaluate=self.evaluate_next)

        if q.dp == DpState.EGRESS_PENDING and q.scheduled_egress != "NONE":
            return [NoOp()]

        return [NoOp()]

    def on_user_input(self, ctx: QProduct, event: object) -> DpState:
        return DpState.CTX_BUILD

    def on_steer(self, ctx: QProduct, event: object, *, config: KernelConfig) -> None:
        self._reset_state(
            participants=self._participants_from_spec(config),
            task=str(getattr(event, "context_text", "") or ""),
        )

    def on_context_complete(self, ctx: QProduct):
        return DpState.EVALUATING, None

    def on_evaluate(self, ctx: QProduct):
        return DpState.EVALUATING, None

    def evaluate_next(self, q: QProduct, run: RunLedger, *, config: KernelConfig) -> list[EgressSymbol]:
        raise NotImplementedError

    # --- Participant resolution ---

    def _participants_from_spec(self, config: KernelConfig) -> list[str]:
        spec = getattr(config, "agent_spec", None) or {}
        wf = (spec.get("workflow") or {}) if isinstance(spec, dict) else {}
        entry_id = str(wf.get("entry") or "moderator").strip()

        def participants(values: object) -> list[str]:
            if not isinstance(values, list):
                return []
            return [
                participant
                for value in values
                if (participant := str(value).strip()) and participant != entry_id
            ]

        # 1) entry node delegates_to (dynamic topology)
        nodes = wf.get("nodes") or []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if str(node.get("id") or "").strip() != entry_id:
                continue
            out = participants(node.get("delegates_to") or [])
            if out:
                return out

        # 2) explicit workflow.participants
        out = participants(wf.get("participants") or [])
        if out:
            return out

        # 3) fallback: workflow.nodes excluding the entry agent
        out: list[str] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_id = str(node.get("id") or node.get("agent") or "").strip()
            if not node_id or node_id == entry_id:
                continue
            out.append(node_id)
        if out:
            return out

        # 4) fallback: agency agents list from MAS spec (exclude entry/self)
        agency = (spec.get("agency") or {}) if isinstance(spec, dict) else {}
        rows = agency.get("agents") or []
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            aid = str(row.get("id") or row.get("name") or "").strip()
            if not aid or aid == entry_id:
                continue
            out.append(aid)
        return out
