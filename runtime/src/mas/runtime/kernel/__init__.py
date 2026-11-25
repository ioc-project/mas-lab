#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Mealy kernel — orchestrator, product state (Q + run ledger), egress gate."""

from typing import TYPE_CHECKING

from mas.runtime.kernel.state import DpState, LifecycleState, QProduct, RunEvent, RunLedger
from mas.runtime.lazy import lazy_exports

# The orchestrator loads on first use. It imports the machines, and the machines import ``kernel.state``: loading it
# here made every machine module depend on the order in which the kernel was first imported.
_EXPORTS = {name: "mas.runtime.kernel.orchestrator" for name in ("RuntimeKernel", "StepResult")}

if TYPE_CHECKING:
    from mas.runtime.kernel.orchestrator import RuntimeKernel, StepResult

__all__ = [
    "DpState",
    "LifecycleState",
    "QProduct",
    "RunEvent",
    "RunLedger",
    "RuntimeKernel",
    "StepResult",
]


__getattr__, __dir__ = lazy_exports(__name__, globals(), _EXPORTS)
