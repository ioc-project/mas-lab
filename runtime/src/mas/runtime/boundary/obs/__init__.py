#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Observability boundary — M_obs operator."""

from typing import TYPE_CHECKING

from mas.runtime.lazy import lazy_exports

# Loaded on first use: the operator imports the kernel, and ``mas.runtime.boundary.obs.binding`` (which the spec
# parser needs) must not pay for it.
_EXPORTS = {"ObservabilityOperator": "mas.runtime.boundary.obs.operator"}

if TYPE_CHECKING:
    from mas.runtime.boundary.obs.operator import ObservabilityOperator

__all__ = ["ObservabilityOperator"]


__getattr__, __dir__ = lazy_exports(__name__, globals(), _EXPORTS)
