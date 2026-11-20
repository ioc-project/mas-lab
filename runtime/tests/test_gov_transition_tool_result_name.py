#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
from __future__ import annotations

from mas.runtime.boundary.gov.transition import build_gov_transition
from mas.runtime.kernel.state import QProduct
from mas.runtime.schema.ingress import EngineIoReturn


def test_ingress_tool_result_carries_tool_name() -> None:
    q = QProduct()
    q.pending_tool_name = "pay_fare"
    q.pending_tool_args = {"amount": 1}
    symbol = EngineIoReturn(correlation_id=1, response_kind="TOOL_RESULT", next_step="LLM_CALL")
    t = build_gov_transition("ingress", symbol, q=q, agent_id="payer")
    assert t.attributes["tool_name"] == "pay_fare"


def test_parallel_tool_results_by_correlation_id() -> None:
    q = QProduct()
    q.pending_tool_name = "other"
    q.pending_tools_by_cid[1] = ("pay_fare", {"n": 1})
    q.pending_tools_by_cid[2] = ("lookup", {"n": 2})
    first = build_gov_transition(
        "ingress",
        EngineIoReturn(correlation_id=1, response_kind="TOOL_RESULT", next_step="LLM_CALL"),
        q=q,
    )
    second = build_gov_transition(
        "ingress",
        EngineIoReturn(correlation_id=2, response_kind="TOOL_RESULT", next_step="LLM_CALL"),
        q=q,
    )
    assert first.attributes["tool_name"] == "pay_fare"
    assert second.attributes["tool_name"] == "lookup"
