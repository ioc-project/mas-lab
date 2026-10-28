#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Single-pass design-pattern plugin — TLA: SinglePassPattern.tla (NOT workflow topology)."""

from __future__ import annotations

from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.egress_gate import emit_scheduled_egress
from mas.runtime.kernel.ingress_step import apply_engine_io_return
from mas.runtime.kernel.response_text import response_text_from_run
from mas.runtime.kernel.state import (
    CtxState,
    DpState,
    LifecycleState,
    ModelState,
    QProduct,
    RunLedger,
    ScheduledEgress,
    ToolState,
)
from mas.runtime.machines.design_pattern.protocol import DesignPatternPlugin
from mas.runtime.schema.egress import EgressSymbol, EmitClientResponse, NoOp, RequestCtxAssembly
from mas.runtime.schema.ingress import (
    CtxAssemblyComplete,
    EngineIoReturn,
    IngressSymbol,
    UserInputReceived,
)


class SinglePassPlugin(DesignPatternPlugin):
    plugin_id = "single_pass@v1"

    def handle_event(
        self,
        q: QProduct,
        run: RunLedger,
        event: IngressSymbol,
        *,
        config: KernelConfig,
    ) -> list[EgressSymbol]:
        if q.ctrl != LifecycleState.RUNNING:
            return []

        if isinstance(event, UserInputReceived) and q.dp == DpState.IDLE:
            q.dp = DpState.CTX_BUILD
            q.ctx = CtxState.COLLECTING
            return [RequestCtxAssembly(collect_id=event.user_turn_id)]

        if isinstance(event, CtxAssemblyComplete) and q.dp == DpState.CTX_BUILD:
            q.dp = DpState.EGRESS_PENDING
            q.ctx = CtxState.COMMITTED
            q.scheduled_egress = "LLM_CALL"
            return emit_scheduled_egress(q, run, config)

        if isinstance(event, EngineIoReturn) and q.dp == DpState.AWAITING_INGRESS:
            return apply_engine_io_return(
                q, run, event, config=config, evaluate=self._evaluate
            )

        return []

    def on_user_input(self, ctx: QProduct, event: object) -> DpState:
        return DpState.CTX_BUILD

    def on_context_complete(self, ctx: QProduct) -> tuple[DpState, ScheduledEgress | None]:
        return DpState.EGRESS_PENDING, "LLM_CALL"

    def on_evaluate(self, ctx: QProduct) -> tuple[DpState, ScheduledEgress | None]:
        return DpState.IDLE, None

    def evaluate_next(
        self,
        q: QProduct,
        run: RunLedger,
        *,
        config: KernelConfig,
    ) -> list[EgressSymbol]:
        if q.dp != DpState.EVALUATING:
            return []
        return self._evaluate(q, run, config)

    def _evaluate(self, q: QProduct, run: RunLedger, config: KernelConfig) -> list[EgressSymbol]:
        if q.dp != DpState.EVALUATING or not run.events:
            return [NoOp()]
        q.model = ModelState.IDLE
        q.tool = ToolState.IDLE
        q.inflight_kind = "NONE"
        q.dp = DpState.IDLE
        q.ctx = CtxState.IDLE
        latest = run.latest()
        content = response_text_from_run(run, fallback=(latest.text if latest is not None else "") or "Done.")
        return [EmitClientResponse(content=content)]
