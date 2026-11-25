#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Ingress and egress symbols (Σ) — typed kernel I/O alphabet."""

from typing import TYPE_CHECKING

from mas.runtime.lazy import lazy_exports

# Loaded on first use: ``mas.runtime.schema.transport`` and its siblings are plain dataclasses and must not pay for
# the pydantic models of the symbols.
_EXPORTS = {
    "EgressKind": "mas.runtime.schema.egress",
    "EgressSymbol": "mas.runtime.schema.egress",
    "IngressSymbol": "mas.runtime.schema.ingress",
}

if TYPE_CHECKING:
    from mas.runtime.schema.egress import EgressKind, EgressSymbol
    from mas.runtime.schema.ingress import IngressSymbol

__all__ = [
    "EgressKind",
    "EgressSymbol",
    "IngressSymbol",
]


__getattr__, __dir__ = lazy_exports(__name__, globals(), _EXPORTS)
