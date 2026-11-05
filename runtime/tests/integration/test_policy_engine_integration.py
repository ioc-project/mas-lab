#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Declarative governance policy engine integration."""

import pytest
from mas.runtime.boundary.gov.exceptions import PolicySkip
from mas.runtime.boundary.gov.policy_engine import (
    ConditionEvaluator,
    GovernancePolicyEngine,
    PolicyDefinition,
    PolicyTrigger,
)
from mas.runtime.schema.governance import GovernanceAction


def test_condition_evaluator_numeric_compare():
    assert ConditionEvaluator.evaluate("budget.used > 5", {"budget": {"used": 10}})


def test_policy_engine_hitl_on_tool_input():
    engine = GovernancePolicyEngine(
        policies=[
            PolicyDefinition(
                name="confirm-delete",
                trigger=PolicyTrigger(on="tool_input", tool="delete_file", condition="args.force == True"),
                action="hitl",
            )
        ]
    )
    action = engine.evaluate_trigger(
        "tool_input",
        {"args": {"force": True}},
        tool_name="delete_file",
    )
    assert action == GovernanceAction.HITL


def test_policy_engine_blacklist_raises_skip():
    engine = GovernancePolicyEngine()
    with pytest.raises(PolicySkip):
        engine.apply_action(GovernanceAction.BLACKLIST, tool_name="dangerous")
