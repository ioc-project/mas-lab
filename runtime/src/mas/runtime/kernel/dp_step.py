#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Dispatch M_dp transitions through the registered design-pattern plugin."""

from __future__ import annotations

from collections.abc import Callable

from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.state import DpState, QProduct, RunLedger, end_turn
from mas.runtime.machines.design_pattern.protocol import DesignPatternPlugin
from mas.runtime.schema.egress import EgressSymbol
from mas.runtime.schema.ingress import IngressSymbol


def step_design_pattern(
    plugin: DesignPatternPlugin,
    q: QProduct,
    run: RunLedger,
    event: IngressSymbol,
    *,
    config: KernelConfig,
    rollback: Callable[[], None],
) -> list[EgressSymbol]:
    from mas.runtime.schema.egress import RaiseBoundaryError

    try:
        return plugin.handle_event(q, run, event, config=config)
    except Exception as exc:
        # A pattern that fails midway must not leave half a step behind: the step is undone (``rollback``
        # puts the state and the ledger back to before the event). The rollback also reopens the waits the
        # event had consumed, and the engine will not answer them again, so a turn in progress ends here,
        # keeping the pending human answers. The client gets the error.
        rollback()
        if q.dp != DpState.IDLE:
            end_turn(q, keep_holds=True)
        return [
            RaiseBoundaryError(
                code="PLUGIN_ERROR",
                message=str(exc) or exc.__class__.__name__,
                recoverable=True,
            )
        ]
