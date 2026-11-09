#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The budget governance plugin counts calls at egress and stops the run at the cap."""

from __future__ import annotations

import pytest
from mas.library.standard.plugins.governance.budget import BudgetPlugin
from mas.runtime.boundary.gov.policy import EgressIntentView
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.coupling import GovDecision
from mas.runtime.spec.gov import build_kernel_config, parse_gov_spec


def _intent(op: str, cid: int) -> EgressIntentView:
    return EgressIntentView(op=op, destructive=False, correlation_id=cid)


def _decide(plugin: BudgetPlugin, op: str, cid: int) -> GovDecision:
    return plugin.evaluate_egress(_intent(op, cid), config=KernelConfig())[0]


def test_llm_calls_stop_at_the_cap() -> None:
    plugin = BudgetPlugin(max_llm_calls=2)
    assert [_decide(plugin, "LLM_CALL", cid) for cid in (1, 2, 3)] == [
        GovDecision.ALLOW,
        GovDecision.ALLOW,
        GovDecision.TERMINATE,
    ]


def test_tool_calls_have_their_own_cap() -> None:
    plugin = BudgetPlugin(max_llm_calls=1, max_tool_calls=1)
    assert _decide(plugin, "TOOL_CALL", 1) is GovDecision.ALLOW
    assert _decide(plugin, "TOOL_CALL", 2) is GovDecision.TERMINATE
    assert _decide(plugin, "LLM_CALL", 3) is GovDecision.ALLOW


def test_a_call_already_counted_passes_again() -> None:
    plugin = BudgetPlugin(max_tool_calls=1)
    assert _decide(plugin, "TOOL_CALL", 7) is GovDecision.ALLOW
    assert _decide(plugin, "TOOL_CALL", 7) is GovDecision.ALLOW
    assert plugin.tool_calls == 1


def test_unset_means_no_limit_and_other_operations_pass() -> None:
    plugin = BudgetPlugin()
    assert all(_decide(plugin, "LLM_CALL", cid) is GovDecision.ALLOW for cid in range(50))
    assert _decide(BudgetPlugin(max_llm_calls=0), "MEMORY_WRITE", 1) is GovDecision.ALLOW


@pytest.mark.parametrize("bad", [-1, True, 1.5, "3"])
def test_a_ceiling_must_be_a_non_negative_integer(bad: object) -> None:
    with pytest.raises(ValueError, match="must be an integer >= 0"):
        BudgetPlugin(max_llm_calls=bad)  # type: ignore[arg-type]


def test_it_is_built_by_name_from_a_governance_list() -> None:
    config = build_kernel_config(parse_gov_spec([{"budget": {"max_llm_calls": 3}}]))
    plugin = config.egress_governance_plugin
    assert isinstance(plugin, BudgetPlugin)
    assert plugin.max_llm_calls == 3
