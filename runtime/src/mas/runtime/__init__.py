#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""MAS Runtime V2 — library package (aligned to TLA MASRuntimeV2Full)."""

from typing import TYPE_CHECKING

import mas.third_party_otel  # noqa: F401
from mas.runtime.lazy import lazy_exports

# The kernel and session types load on first use: importing any ``mas.runtime.*`` submodule runs this file, and
# loading the kernel here made every ``mas-ctl`` command pay for it (about 0.4 s) whether it ran a session or not.
_EXPORTS = {
    "RuntimeKernel": "mas.runtime.kernel.orchestrator",
    "StepResult": "mas.runtime.kernel.orchestrator",
    "IngressSymbol": "mas.runtime.schema.ingress",
    "EgressSymbol": "mas.runtime.schema.egress",
    "QProduct": "mas.runtime.kernel.state",
    "LifecycleState": "mas.runtime.kernel.state",
    "DpState": "mas.runtime.kernel.state",
    "ManifestRef": "mas.runtime.session",
    "Session": "mas.runtime.session",
    "SessionLineage": "mas.runtime.session",
    "SessionStatus": "mas.runtime.session",
}

if TYPE_CHECKING:
    from mas.runtime.kernel.orchestrator import RuntimeKernel, StepResult
    from mas.runtime.kernel.state import DpState, LifecycleState, QProduct
    from mas.runtime.schema.egress import EgressSymbol
    from mas.runtime.schema.ingress import IngressSymbol
    from mas.runtime.session import ManifestRef, Session, SessionLineage, SessionStatus

__all__ = [
    "RuntimeKernel",
    "StepResult",
    "IngressSymbol",
    "EgressSymbol",
    "QProduct",
    "LifecycleState",
    "DpState",
    "ManifestRef",
    "Session",
    "SessionLineage",
    "SessionStatus",
]


__getattr__, __dir__ = lazy_exports(__name__, globals(), _EXPORTS)
