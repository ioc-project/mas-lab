#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""ReAct design-pattern plugin (reference) — TLA: ReactPattern.tla."""

from __future__ import annotations

from mas.runtime.kernel.client_response import client_response_from_run
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.egress_gate import emit_scheduled_egress, schedule_tool_egress
from mas.runtime.kernel.ingress_step import apply_engine_io_return
from mas.runtime.kernel.parallel_tools import schedule_parallel_tools_egress
from mas.runtime.kernel.state import (
    DpState,
    LifecycleState,
    QProduct,
    RunLedger,
    ScheduledEgress,
)
from mas.runtime.machines.context import ctx_on_assembly_complete, ctx_on_cycle_reset, ctx_on_user_input
from mas.runtime.machines.design_pattern.protocol import DesignPatternPlugin
from mas.runtime.machines.memory import memory_on_evaluate
from mas.runtime.machines.model import model_on_evaluate
from mas.runtime.machines.tool import tool_on_evaluate
from mas.runtime.schema.egress import (
    EgressSymbol,
    NoOp,
    RequestCtxAssembly,
)
from mas.runtime.schema.ingress import (
    CtxAssemblyComplete,
    EngineIoReturn,
    IngressSymbol,
    ToolCallSpec,
    UserInputReceived,
)


class ReactPlugin(DesignPatternPlugin):
    plugin_id = "react@v1"

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
            q.hitl_skip_committed = False
            q.hitl_block_committed = False
            q.hitl_tools_approved_turn = False
            q.hitl_results_approved_turn = False
            q.dp = self.on_user_input(q, event)
            q.ctx = ctx_on_user_input(q.ctx)
            return [RequestCtxAssembly(collect_id=event.user_turn_id)]

        if isinstance(event, CtxAssemblyComplete) and q.dp == DpState.CTX_BUILD:
            q.dp, scheduled = self.on_context_complete(q)
            q.ctx = ctx_on_assembly_complete(q.ctx)
            q.scheduled_egress = scheduled  # type: ignore[assignment]
            return emit_scheduled_egress(q, run, config)

        if q.dp == DpState.EGRESS_PENDING and q.scheduled_egress != "NONE":
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
        last = run.latest()
        if last is None:
            return [NoOp()]
        q.model = model_on_evaluate(q.model)
        q.tool = tool_on_evaluate(q.tool)
        q.memory = memory_on_evaluate(q.memory)
        q.ctx = ctx_on_cycle_reset(q.ctx)

        if last.next_step == "TOOL_CALL":
            return schedule_tool_egress(q, run, config)

        if last.next_step == "PARALLEL_TOOL_CALLS":
            specs = [
                ToolCallSpec(
                    tool_name=str(row.get("tool_name") or "tool"),
                    tool_arguments=dict(row.get("tool_arguments") or {}),
                )
                for row in q.parallel_tool_batch
            ]
            q.parallel_tool_batch = []
            return schedule_parallel_tools_egress(q, run, config, specs)

        if last.next_step == "LLM_CALL":
            q.scheduled_egress = "LLM_CALL"
            q.dp = DpState.EGRESS_PENDING
            return emit_scheduled_egress(q, run, config)

        if last.next_step == "DELEGATE":
            if config.enable_memory_egress:
                q.scheduled_egress = "MEMORY_OP"
                q.dp = DpState.EGRESS_PENDING
                return emit_scheduled_egress(q, run, config)
            if config.enable_transport_egress:
                q.scheduled_egress = "TRANSPORT_MSG"
                q.dp = DpState.EGRESS_PENDING
                return emit_scheduled_egress(q, run, config)

        q.dp = DpState.IDLE
        q.scheduled_egress = "NONE"
        return [client_response_from_run(run)]
