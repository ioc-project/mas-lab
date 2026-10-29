#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Apply operator/peer steer — M_dp ingress for OperatorSteerReceived."""

from __future__ import annotations

from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.state import DpState, LifecycleState, QProduct
from mas.runtime.machines.context import ctx_on_user_input
from mas.runtime.machines.design_pattern.protocol import DesignPatternPlugin
from mas.runtime.schema.egress import EgressSymbol, RequestCtxAssembly
from mas.runtime.schema.ingress import OperatorSteerReceived


def step_steer(
    q: QProduct,
    event: OperatorSteerReceived,
    *,
    plugin: DesignPatternPlugin,
    config: KernelConfig,
) -> list[EgressSymbol]:
    """Reset the pattern and request context assembly. No-op unless RUNNING."""
    if q.ctrl != LifecycleState.RUNNING:
        return []
    nxt = plugin.on_steer(q, event, config=config)
    q.dp = nxt if nxt is not None else DpState.CTX_BUILD
    q.ctx = ctx_on_user_input(q.ctx)
    return [
        RequestCtxAssembly(
            collect_id=event.steer_id,
            operator_context=event.context_text,
        )
    ]
