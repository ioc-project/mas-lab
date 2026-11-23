#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Apply operator/peer steer — M_dp ingress for OperatorSteerReceived."""

from __future__ import annotations

from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.state import DpState, LifecycleState, QProduct, reset_turn_scope
from mas.runtime.machines.context import ctx_on_user_input
from mas.runtime.machines.design_pattern.protocol import DesignPatternPlugin
from mas.runtime.machines.gov import gov_is_hitl_pending
from mas.runtime.schema.egress import EgressSymbol, RaiseBoundaryError, RequestCtxAssembly
from mas.runtime.schema.ingress import OperatorSteerReceived


def step_steer(
    q: QProduct,
    event: OperatorSteerReceived,
    *,
    plugin: DesignPatternPlugin,
    config: KernelConfig,
) -> list[EgressSymbol]:
    """Reset the pattern and request context assembly. No-op unless RUNNING.

    A steer while a human answer is pending is refused with a recoverable boundary error (the hold stays as it is).
    A steer from idle starts a cycle, so it starts a clean turn like a user input.
    """
    if q.ctrl != LifecycleState.RUNNING:
        return []
    if gov_is_hitl_pending(q):
        return [
            RaiseBoundaryError(
                code="STEER_REFUSED",
                recoverable=True,
                message="a human answer is pending; answer it before steering",
            )
        ]
    if q.dp == DpState.IDLE:
        reset_turn_scope(q)
    nxt = plugin.on_steer(q, event, config=config)
    q.dp = nxt if nxt is not None else DpState.CTX_BUILD
    q.ctx = ctx_on_user_input(q.ctx)
    return [
        RequestCtxAssembly(
            collect_id=event.steer_id,
            operator_context=event.context_text,
        )
    ]
