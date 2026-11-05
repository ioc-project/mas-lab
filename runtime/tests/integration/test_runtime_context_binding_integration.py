#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Runtime context binding integration."""

from mas.runtime.boundary.obs.operator import ObservabilityOperator
from mas.runtime.kernel.runtime_context import get_governance_observability, runtime_binding


def test_runtime_binding_scopes_observability():
    obs = ObservabilityOperator()
    with runtime_binding(None, obs):
        assert get_governance_observability() is obs
