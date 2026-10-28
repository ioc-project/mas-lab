#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Pure Mealy kernel orchestrator — transition(Σ_in) → list[Σ_out]."""

from __future__ import annotations

from dataclasses import dataclass, field

from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.dp_step import step_design_pattern
from mas.runtime.kernel.hitl_step import apply_hitl_resolve
from mas.runtime.kernel.state import DpState, LifecycleState, QProduct, RunLedger
from mas.runtime.machines.context import ctx_on_user_input
from mas.runtime.machines.design_pattern.protocol import DesignPatternPlugin
from mas.runtime.registry import get_registry
from mas.runtime.schema.egress import EgressSymbol, RequestCtxAssembly
from mas.runtime.schema.hitl import HitlResolveChoice
from mas.runtime.schema.ingress import (
    HitlApprove,
    HitlResolve,
    IngressSymbol,
    OperatorSteerReceived,
)

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

    def __post_init__(self) -> None:
        if self.plugin is None:
            self.plugin = _load_design_pattern_plugin(self.config.pattern_plugin_id)

    def transition(self, event: IngressSymbol) -> StepResult:
        from mas.runtime.machines.lifecycle import step_lifecycle
        from mas.runtime.session.cow import writable

        self.q = writable(self.q)
        self.run = writable(self.run)

        egress: list[EgressSymbol] = []
        egress.extend(step_lifecycle(self.q, event))

        if self.q.ctrl.value == "STOPPED":
            return StepResult(egress=egress)

        if isinstance(event, OperatorSteerReceived) and self.q.ctrl == LifecycleState.RUNNING:
            self.q.dp = DpState.CTX_BUILD
            self.q.ctx = ctx_on_user_input(self.q.ctx)
            plugin = self.plugin
            if plugin is None:
                raise RuntimeError("kernel has no design pattern plugin")
            plugin.on_steer(self.q, event, config=self.config)
            egress.append(
                RequestCtxAssembly(
                    collect_id=event.steer_id,
                    operator_context=event.context_text,
                )
            )
            return StepResult(egress=egress)

        if isinstance(event, (HitlResolve, HitlApprove)):
            if isinstance(event, HitlApprove):
                event = HitlResolve(
                    request_id=self.q.hitl_request_id,
                    resolution=HitlResolveChoice.SCHEDULE,
                )
            egress.extend(
                apply_hitl_resolve(
                    self.q,
                    self.run,
                    event,
                    config=self.config,
                    evaluate=self._evaluate_loop,
                )
            )
            if self.q.dp == DpState.EVALUATING:
                assert self.plugin is not None
                for _ in range(MAX_EVALUATE_STEPS):
                    extra = self.plugin.evaluate_next(self.q, self.run, config=self.config)
                    egress.extend(extra)
                    if self.q.dp != DpState.EVALUATING:
                        break
            return StepResult(egress=egress)

        assert self.plugin is not None
        egress.extend(
            step_design_pattern(self.plugin, self.q, self.run, event, config=self.config)
        )
        if self.q.dp == DpState.EVALUATING:
            for _ in range(MAX_EVALUATE_STEPS):
                extra = self.plugin.evaluate_next(self.q, self.run, config=self.config)
                egress.extend(extra)
                if self.q.dp != DpState.EVALUATING:
                    break
        return StepResult(egress=egress)

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
