#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Pure Mealy kernel orchestrator — transition(Σ_in) → list[Σ_out]."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.dp_step import step_design_pattern
from mas.runtime.kernel.hitl_step import apply_hitl_resolve
from mas.runtime.kernel.state import (
    DpState,
    LifecycleState,
    QProduct,
    RunLedger,
    copy_state,
    end_turn,
    reset_turn_scope,
    restore_state,
)
from mas.runtime.kernel.steer_step import step_steer
from mas.runtime.machines.design_pattern.protocol import DesignPatternPlugin
from mas.runtime.machines.gov import gov_first_hold_id, gov_is_hitl_pending
from mas.runtime.registry import get_registry
from mas.runtime.schema.egress import EgressSymbol, RaiseBoundaryError
from mas.runtime.schema.hitl import HitlResolveChoice
from mas.runtime.schema.ingress import (
    HitlApprove,
    HitlResolve,
    IngressSymbol,
    LifecyclePause,
    LifecycleResume,
    OperatorSteerReceived,
    UserInputReceived,
)

logger = logging.getLogger(__name__)

INTERRUPTED_BY_PAUSE = (
    "This call was interrupted by a pause. It may have executed; its outcome is unknown. "
    "Do not repeat it unless you have checked that it did not run."
)


def _approves(answer: HitlResolve) -> bool:
    return answer.resolution in {HitlResolveChoice.ALLOW, HitlResolveChoice.SCHEDULE}


# Upper bound on evaluate_next iterations per transition to prevent runaway loops.
MAX_EVALUATE_STEPS = 8


def _load_design_pattern_plugin(pattern_plugin_id: str | None) -> DesignPatternPlugin:
    registry = get_registry()
    key = pattern_plugin_id or registry.default_for("design_pattern")
    info = registry.get("design_pattern", key)
    if info is None:
        raise KeyError(f"unknown design pattern plugin: {key!r}")
    return info.load_class()()


@dataclass
class StepResult:
    egress: list[EgressSymbol] = field(default_factory=list)


@dataclass
class RuntimeKernel:
    """Synchronous kernel step; M_dp dispatches to registered pattern plugins."""

    q: QProduct = field(default_factory=QProduct)
    run: RunLedger = field(default_factory=RunLedger)
    config: KernelConfig = field(default_factory=KernelConfig)
    plugin: DesignPatternPlugin | None = None
    _copy_warned: bool = field(default=False, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.plugin is None:
            self.plugin = _load_design_pattern_plugin(self.config.pattern_plugin_id)

    def _restore(self, saved: tuple[QProduct, tuple[int, int]] | None) -> None:
        """Put the state and the ledger back to the snapshot a ``transition`` took when it began."""
        if saved is None:
            return  # no snapshot could be taken (see ``transition``): nothing to restore
        restore_state(self.q, saved[0])
        self.run.rewind(saved[1])

    def _snapshot(self) -> tuple[QProduct, tuple[int, int]] | None:
        """A copy of the state and the ledger's mark, or None when the state cannot be copied.

        The snapshot is a deep copy, so every field of the state must be plain data. A design pattern that keeps
        a lock, a task or a client in ``dp_data`` would otherwise fail every step of the kernel. It is warned
        about once and the step runs without atomicity: that is worse than the rollback and better than no kernel.
        """
        try:
            return copy_state(self.q), self.run.mark()
        except Exception as exc:  # noqa: BLE001 - copy.deepcopy raises whatever the held object raises
            if not self._copy_warned:
                self._copy_warned = True
                logger.warning(
                    "the kernel state cannot be copied (%s: %s): steps run without a rollback; keep plain data "
                    "(dicts, lists, numbers, strings) in dp_data",
                    type(exc).__name__,
                    exc,
                )
            return None

    def set_running_spec(self, spec: dict | None) -> None:
        """The session's running spec as of this step (read by governance; not part of the snapshot)."""
        self.q.running_spec = spec

    def transition(self, event: IngressSymbol) -> StepResult:
        """One atomic step. Whatever ends it idle (answer, block, pause) leaves no flow state behind.

        Atomic means the product state and the run ledger either take the whole step or stay as they were: an
        exception from a plugin or machine mid-step is re-raised after both are put back, so the next event
        meets a consistent kernel. (Effects outside them, such as a plugin's own counters, are not undone.)
        """
        # Local to this call: a nested or concurrent ``transition`` must not replace the snapshot of this one.
        saved = self._snapshot()
        try:
            result = self._step(event, lambda: self._restore(saved))
        except BaseException:
            self._restore(saved)
            raise
        if self.q.dp == DpState.IDLE and not gov_is_hitl_pending(self.q):
            end_turn(self.q)
        return result

    def _step(self, event: IngressSymbol, rollback: Callable[[], None]) -> StepResult:
        from mas.runtime.machines.lifecycle import step_lifecycle
        from mas.runtime.session.cow import writable

        self.q = writable(self.q)
        self.run = writable(self.run)

        egress: list[EgressSymbol] = []
        pausing = isinstance(event, LifecyclePause) and self.q.ctrl == LifecycleState.RUNNING
        # A result parked in an ingress hold is answered already (its hold releases it); the others are cancelled.
        parked = {int(h.ingress_return.get("correlation_id", 0)) for h in self.q.hitl_holds.values() if h.is_ingress}
        cancelled = (
            [w.correlation_id for w in self.q.outbound_waits if w.kind == "TOOL" and w.correlation_id not in parked]
            if pausing
            else []
        )
        calls = dict(self.q.pending_tools_by_cid)
        slots = dict(self.q.call_slots)
        egress.extend(step_lifecycle(self.q, event))
        if pausing and self.q.ctrl == LifecycleState.PAUSED:
            # A pause does not stop a tool that is already running, so the outcome of a call it interrupted is
            # unknown. The call still gets an answer in the ledger (a tool call without a result makes the next
            # model prompt invalid): an error row that says so, never a normal result. If the real result
            # arrives later it is dropped (see ``apply_engine_io_return``).
            for cid in cancelled:
                name, args = calls.get(cid, ("", {}))
                self.run.append_placeholder(
                    cid=cid,
                    text=INTERRUPTED_BY_PAUSE,
                    tool_name=name,
                    tool_arguments=args,
                    call_id=slots.get(cid, ""),
                    response_kind="ERROR",
                )
                self.q.pending_tools_by_cid.pop(cid, None)
                self.q.call_slots.pop(cid, None)

        if self.q.ctrl != LifecycleState.RUNNING:
            # Paused or stopped: only lifecycle events move the kernel. Pause cancelled every wait but
            # kept the holds; a human answer to one is remembered and applied on resume, not now.
            if self.q.ctrl == LifecycleState.PAUSED and isinstance(event, (HitlResolve, HitlApprove)):
                self._defer_hitl_answer(event)
            return StepResult(egress=egress)

        if isinstance(event, LifecycleResume):
            # The held operations whose answer came while paused are released now, in request order, once.
            for request_id in sorted(self.q.hitl_holds):
                hold = self.q.hitl_holds.get(request_id)
                if hold is None or not hold.deferred:
                    continue
                answer = HitlResolve.model_validate(hold.deferred)
                hold.deferred = {}
                egress.extend(self._resolve_hitl(answer))
            return StepResult(egress=egress)

        if isinstance(event, OperatorSteerReceived):
            plugin = self.plugin
            if plugin is None:
                raise RuntimeError("kernel has no design pattern plugin")
            egress.extend(step_steer(self.q, event, plugin=plugin, config=self.config))
            if self.q.ctrl == LifecycleState.RUNNING:
                return StepResult(egress=egress)

        if isinstance(event, UserInputReceived) and self.q.dp == DpState.IDLE:
            # A new turn starts clean, whichever design pattern runs it.
            reset_turn_scope(self.q)

        if isinstance(event, (HitlResolve, HitlApprove)):
            answer = self._as_resolve(event)
            if answer is None:
                egress.append(
                    RaiseBoundaryError(
                        code="HITL_APPROVE_AMBIGUOUS",
                        recoverable=True,
                        message="an approval without a request id is refused unless exactly one request is pending",
                    )
                )
            else:
                egress.extend(self._resolve_hitl(answer))
            return StepResult(egress=egress)

        assert self.plugin is not None
        egress.extend(step_design_pattern(self.plugin, self.q, self.run, event, config=self.config, rollback=rollback))
        if self.q.dp == DpState.EVALUATING:
            for _ in range(MAX_EVALUATE_STEPS):
                extra = self.plugin.evaluate_next(self.q, self.run, config=self.config)
                egress.extend(extra)
                if self.q.dp != DpState.EVALUATING:
                    break
        return StepResult(egress=egress)

    def _as_resolve(self, event: HitlResolve | HitlApprove) -> HitlResolve | None:
        """The answer for a request. A bare approval names no request, so it is taken only when exactly one is
        pending: with several, approving "the oldest" could approve a call nobody looked at."""
        if not isinstance(event, HitlApprove):
            return event
        if len(self.q.hitl_holds) != 1:
            logger.warning("HitlApprove names no request and %d are pending; refused", len(self.q.hitl_holds))
            return None
        return HitlResolve(request_id=gov_first_hold_id(self.q), resolution=HitlResolveChoice.SCHEDULE)

    def _defer_hitl_answer(self, event: HitlResolve | HitlApprove) -> None:
        answer = self._as_resolve(event)
        hold = self.q.hitl_holds.get(answer.request_id) if answer is not None else None
        if hold is None:
            return
        if hold.deferred:
            # One answer per hold, as when running. A deny always beats an allow, in either order, so a
            # refusal given during the pause cannot be undone by a later approval.
            kept = HitlResolve.model_validate(hold.deferred)
            if _approves(kept) and not _approves(answer):
                hold.deferred = answer.model_dump(mode="json")
                return
            logger.warning(
                "HITL request %s already has an answer (%s) while paused; ignoring %s",
                hold.request_id,
                kept.resolution.value,
                answer.resolution.value,
            )
            return
        hold.deferred = answer.model_dump(mode="json")

    def _resolve_hitl(self, event: HitlResolve) -> list[EgressSymbol]:
        egress = apply_hitl_resolve(
            self.q,
            self.run,
            event,
            config=self.config,
            evaluate=self._evaluate_loop,
        )
        if self.q.dp == DpState.EVALUATING:
            assert self.plugin is not None
            for _ in range(MAX_EVALUATE_STEPS):
                extra = self.plugin.evaluate_next(self.q, self.run, config=self.config)
                egress.extend(extra)
                if self.q.dp != DpState.EVALUATING:
                    break
        return egress

    def _evaluate_loop(self, q: QProduct, run: RunLedger, config: KernelConfig) -> list[EgressSymbol]:
        assert self.plugin is not None
        out: list[EgressSymbol] = []
        for _ in range(MAX_EVALUATE_STEPS):
            extra = self.plugin.evaluate_next(q, run, config=config)
            out.extend(extra)
            if q.dp != DpState.EVALUATING:
                break
        return out

    def snapshot(self) -> dict:
        from mas.runtime.kernel.state_serialize import q_product_to_dict, run_to_dict

        return {
            "q": q_product_to_dict(self.q),
            "run": run_to_dict(self.run),
            "pattern_plugin_id": self.config.pattern_plugin_id,
        }

    def freeze_cow(self):
        from mas.runtime.session.cow import freeze_kernel

        return freeze_kernel(self)

    def restore(self, data: dict) -> None:
        from mas.runtime.kernel.state_serialize import q_product_from_dict, run_from_dict

        self.q = q_product_from_dict(data["q"])
        self.run = run_from_dict(data["run"])
