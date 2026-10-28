#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Design-pattern plugins — implement M_dp Mealy protocol (DesignPatternPlugin).

Patterns (ReAct, CoT, SinglePass, …) register with the kernel; the kernel never
imports pattern-specific logic. See runtime/docs/design-patterns.md.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol

from mas.runtime.kernel.state import DpState, QProduct, RunLedger, ScheduledEgress
from mas.runtime.schema.egress import EgressSymbol
from mas.runtime.schema.governance import GovPolicyProfile
from mas.runtime.schema.ingress import IngressSymbol

ContextView = QProduct


class KernelConfigProtocol(Protocol):
    pattern_plugin_id: str
    gov_policy_profile: GovPolicyProfile
    gov_block_destructive: bool
    gov_trigger_destructive: bool
    hitl_on_tool: bool
    enable_memory_egress: bool
    enable_transport_egress: bool


class MealyPlugin(Protocol):
    """Protocol every design-pattern plugin must satisfy."""

    plugin_id: str

    def handle_event(
        self,
        ctx: ContextView,
        run: RunLedger,
        event: IngressSymbol,
        *,
        config: KernelConfigProtocol,
    ) -> list[EgressSymbol]: ...

    def on_user_input(self, ctx: ContextView, event: object) -> DpState: ...

    def on_context_complete(self, ctx: ContextView) -> tuple[DpState, ScheduledEgress | None]: ...

    def on_evaluate(self, ctx: ContextView) -> tuple[DpState, ScheduledEgress | None]: ...

    def on_hitl_required(self, ctx: ContextView) -> DpState: ...

    def evaluate_next(
        self,
        ctx: ContextView,
        run: RunLedger,
        *,
        config: KernelConfigProtocol,
    ) -> list[EgressSymbol]:
        """Drive the next evaluation step (the kernel calls this on its hot loop)."""
        ...


class DesignPatternPlugin(ABC):
    """Base class for in-process pattern plugins."""

    plugin_id: str
    STEER_MODES: frozenset[str] = frozenset({"after", "preempt", "replace"})

    @abstractmethod
    def handle_event(
        self,
        ctx: ContextView,
        run: RunLedger,
        event: IngressSymbol,
        *,
        config: KernelConfigProtocol,
    ) -> list[EgressSymbol]:
        """Apply one ingress symbol to M_dp (and related δ tables)."""

    @abstractmethod
    def on_user_input(self, ctx: ContextView, event: object) -> DpState:
        """Typically transition IDLE → CTX_BUILD."""

    @abstractmethod
    def on_context_complete(self, ctx: ContextView) -> tuple[DpState, ScheduledEgress | None]:
        """Typically CTX_BUILD → EGRESS_PENDING with LLM_CALL intent."""

    @abstractmethod
    def on_evaluate(self, ctx: ContextView) -> tuple[DpState, ScheduledEgress | None]:
        """Read τ tail next_step; schedule TOOL_CALL or STOP (IDLE)."""

    def on_steer(
        self,
        ctx: ContextView,
        event: object,
        *,
        config: KernelConfigProtocol,
    ) -> None:
        """Optional. Deterministic patterns reset their script to the steer text."""
        return None

    def evaluate_next(
        self,
        ctx: ContextView,
        run: RunLedger,
        *,
        config: KernelConfigProtocol,
    ) -> list[EgressSymbol]:
        return []
