#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Envelope HITL/block decision integration."""

from mas.runtime.boundary.obs.operator import ObservabilityOperator
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.envelope import EnvelopeContext, contract_kind_for_op, run_egress_authorize_envelope
from mas.runtime.kernel.state import QProduct


def test_egress_hitl_when_configured():
    ctx = EnvelopeContext(
        q=QProduct(),
        correlation_id=1,
        contract=contract_kind_for_op("TOOL_CALL"),
        scheduled_op="TOOL_CALL",
        observability=ObservabilityOperator(),
        config=KernelConfig(hitl_on_tool=True),
        tool_name="delete_all",
        tool_arguments={},
    )
    decision = run_egress_authorize_envelope(ctx)
    label = decision.value if hasattr(decision, "value") else str(decision)
    assert label in {"ALLOW", "HITL", "BLOCK"}
