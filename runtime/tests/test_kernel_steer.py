#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The kernel turns OperatorSteerReceived into context assembly while RUNNING."""

from __future__ import annotations

from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.orchestrator import RuntimeKernel
from mas.runtime.kernel.state import CtxState, DpState, LifecycleState, QProduct, RunLedger
from mas.runtime.kernel.steer_step import step_steer
from mas.runtime.machines.design_pattern.protocol import DesignPatternPlugin
from mas.runtime.schema.egress import EgressSymbol, RequestCtxAssembly
from mas.runtime.schema.ingress import IngressSymbol, LifecyclePause, OperatorSteerReceived


class _RecordingPattern(DesignPatternPlugin):
    plugin_id = "recording-steer"

    def __init__(self) -> None:
        self.steers: list[object] = []

    def handle_event(
        self,
        ctx: QProduct,
        run: RunLedger,
        event: IngressSymbol,
        *,
        config: object,
    ) -> list[EgressSymbol]:
        del ctx, run, event, config
        return []

    def on_user_input(self, ctx: QProduct, event: object) -> DpState:
        del ctx, event
        return DpState.CTX_BUILD

    def on_context_complete(self, ctx: QProduct) -> tuple[DpState, None]:
        del ctx
        return DpState.EVALUATING, None

    def on_evaluate(self, ctx: QProduct) -> tuple[DpState, None]:
        del ctx
        return DpState.EVALUATING, None

    def on_steer(self, ctx: QProduct, event: object, *, config: object) -> DpState:
        del ctx, config
        self.steers.append(event)
        return DpState.CTX_BUILD


def _steer() -> OperatorSteerReceived:
    return OperatorSteerReceived(
        steer_id="steer-1",
        context_text="go via Lyon",
        source="peer",
        sender="planner",
        intent="steer",
    )


def test_step_steer_resets_context_to_collecting() -> None:
    plugin = _RecordingPattern()
    q = QProduct()
    q.ctrl = LifecycleState.RUNNING
    q.ctx = CtxState.COMMITTED
    step_steer(q, _steer(), plugin=plugin, config=KernelConfig())
    assert q.ctx is CtxState.COLLECTING
    assert plugin.steers


def test_kernel_steer_calls_on_steer_when_running() -> None:
    plugin = _RecordingPattern()
    kernel = RuntimeKernel(plugin=plugin)
    event = _steer()
    result = kernel.transition(event)
    assert plugin.steers == [event]
    assert kernel.q.dp is DpState.CTX_BUILD
    assert kernel.q.ctrl is LifecycleState.RUNNING
    assert isinstance(result.egress[-1], RequestCtxAssembly)
    assert result.egress[-1].collect_id == "steer-1"
    assert result.egress[-1].operator_context == "go via Lyon"


def test_kernel_steer_uses_on_steer_return_state() -> None:
    class _IdleOnSteer(_RecordingPattern):
        def on_steer(self, ctx: QProduct, event: object, *, config: object) -> DpState:
            del ctx, config
            self.steers.append(event)
            return DpState.IDLE

    plugin = _IdleOnSteer()
    kernel = RuntimeKernel(plugin=plugin)
    kernel.transition(_steer())
    assert plugin.steers
    assert kernel.q.dp is DpState.IDLE


def test_kernel_steer_ignored_when_not_running() -> None:
    plugin = _RecordingPattern()
    kernel = RuntimeKernel(plugin=plugin)
    kernel.transition(LifecyclePause())
    assert kernel.q.ctrl is LifecycleState.PAUSED
    result = kernel.transition(_steer())
    assert plugin.steers == []
    assert kernel.q.ctrl is LifecycleState.PAUSED
    assert kernel.q.dp is DpState.IDLE
    assert not any(isinstance(item, RequestCtxAssembly) for item in result.egress)
