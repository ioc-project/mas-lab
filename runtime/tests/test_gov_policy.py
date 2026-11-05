#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Egress/ingress governance decision + reason coverage.

Regression suite for boundary/gov/policy.py: every (decision, reason) pair is
now computed together in one function rather than reconstructed afterward by
a second, separately-maintained function — these tests exist because the
previous separate-reconstruction design shipped three undetected bugs (an
ignored block_destructive flag, an ignored hitl_on_tool flag, and a
profile-driven decision mislabeled as declarative-policy-triggered), and none
of them had a single test.
"""

from __future__ import annotations

from mas.runtime.boundary.gov.plugin import egress_from_config, evaluate_egress_at_chokepoint
from mas.runtime.boundary.gov.policy import (
    EgressIntentView,
    egress_governance_outcome,
    ingress_governance_outcome,
    resolve_egress_governance,
)
from mas.runtime.boundary.gov.policy_engine import GovernancePolicyEngine
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.schema.governance import GovernanceAction, GovIngressProfile, GovPolicyProfile


def _intent(*, destructive: bool = False, op: str = "TOOL_CALL", tool_name: str = "lookup") -> EgressIntentView:
    return EgressIntentView(op=op, destructive=destructive, correlation_id=1, tool_name=tool_name)


# --- egress_governance_outcome: every profile, both destructive states ------


def test_permissive_always_allows() -> None:
    action, label, reason = egress_governance_outcome(
        _intent(destructive=True),
        profile=GovPolicyProfile.PERMISSIVE,
        block_destructive=True,
    )
    assert action == GovernanceAction.ALLOW
    assert "permissive" in reason


def test_log_all_always_logs() -> None:
    action, label, reason = egress_governance_outcome(
        _intent(destructive=False),
        profile=GovPolicyProfile.LOG_ALL,
        block_destructive=False,
    )
    assert action == GovernanceAction.LOG
    assert "log-all" in reason


def test_non_destructive_always_allows_regardless_of_profile() -> None:
    for profile in (
        GovPolicyProfile.BLOCK_DESTRUCTIVE,
        GovPolicyProfile.HITL_DESTRUCTIVE,
        GovPolicyProfile.MODIFY_DESTRUCTIVE,
        GovPolicyProfile.TERMINATE_DESTRUCTIVE,
        GovPolicyProfile.SKIP_DESTRUCTIVE,
        GovPolicyProfile.BLACKLIST_DESTRUCTIVE,
        GovPolicyProfile.RETRY_DESTRUCTIVE,
    ):
        action, label, reason = egress_governance_outcome(
            _intent(destructive=False),
            profile=profile,
            block_destructive=True,
        )
        assert action == GovernanceAction.ALLOW, profile
        assert "non-destructive" in reason, profile


def test_block_destructive_blocks_when_flag_on() -> None:
    action, label, reason = egress_governance_outcome(
        _intent(destructive=True),
        profile=GovPolicyProfile.BLOCK_DESTRUCTIVE,
        block_destructive=True,
    )
    assert action == GovernanceAction.BLOCK
    assert "destructive" in reason


def test_block_destructive_allows_when_flag_off_regression() -> None:
    """Regression: describe_egress_reason used to ignore block_destructive
    entirely and always say "action is destructive under the block-destructive
    policy" even when the flag being off meant the real decision was ALLOW."""
    action, label, reason = egress_governance_outcome(
        _intent(destructive=True),
        profile=GovPolicyProfile.BLOCK_DESTRUCTIVE,
        block_destructive=False,
    )
    assert action == GovernanceAction.ALLOW
    assert "block_destructive=False" in reason


def test_destructive_profiles_act_regardless_of_block_destructive_flag() -> None:
    """Only BLOCK_DESTRUCTIVE is gated by block_destructive; every other
    destructive-only profile always acts once destructive=True."""
    for profile, expected in (
        (GovPolicyProfile.HITL_DESTRUCTIVE, GovernanceAction.HITL),
        (GovPolicyProfile.MODIFY_DESTRUCTIVE, GovernanceAction.MODIFY),
        (GovPolicyProfile.TERMINATE_DESTRUCTIVE, GovernanceAction.TERMINATE),
        (GovPolicyProfile.SKIP_DESTRUCTIVE, GovernanceAction.SKIP),
        (GovPolicyProfile.BLACKLIST_DESTRUCTIVE, GovernanceAction.BLACKLIST),
        (GovPolicyProfile.RETRY_DESTRUCTIVE, GovernanceAction.RETRY),
    ):
        action, _, _ = egress_governance_outcome(
            _intent(destructive=True),
            profile=profile,
            block_destructive=False,
        )
        assert action == expected, profile


# --- resolve_egress_governance: declarative policy layer --------------------


def test_declarative_policy_match_uses_authored_message() -> None:
    engine = GovernancePolicyEngine.from_yaml(
        {
            "policies": [
                {
                    "name": "forbidden-destination",
                    "trigger": {
                        "on": "tool_input",
                        "tool": "*",
                        "condition": 'arguments.destination == "Shadowmere"',
                        "evaluation": "deterministic",
                    },
                    "action": "block",
                    "params": {"message": "Restricted destination: Shadowmere"},
                }
            ]
        }
    )
    intent = EgressIntentView(
        op="TOOL_CALL",
        destructive=False,
        correlation_id=1,
        tool_name="lookup",
        tool_arguments={"destination": "Shadowmere"},
    )
    action, policy_name, reason = resolve_egress_governance(
        intent,
        profile=GovPolicyProfile.PERMISSIVE,
        block_destructive=False,
        policy_engine=engine,
    )
    assert action == GovernanceAction.BLOCK
    assert policy_name == "forbidden-destination"
    assert reason == "Restricted destination: Shadowmere"


def test_declarative_policy_no_match_falls_through_to_profile_regression() -> None:
    """Regression: describe_egress_reason used to assume ANY non-ALLOW decision
    with a policy_engine configured came from a declarative policy match, even
    when find_matching_policy found nothing and the decision actually came
    from the profile fallback — mislabeling it "a declarative policy ...
    triggered" instead of the real block-destructive-profile explanation."""
    engine = GovernancePolicyEngine.from_yaml(
        {
            "policies": [
                {
                    "name": "unrelated-policy",
                    "trigger": {
                        "on": "tool_input",
                        "tool": "*",
                        "condition": 'arguments.destination == "Nowhere"',
                        "evaluation": "deterministic",
                    },
                    "action": "block",
                    "params": {},
                }
            ]
        }
    )
    intent = EgressIntentView(
        op="TOOL_CALL",
        destructive=True,
        correlation_id=1,
        tool_name="lookup",
        tool_arguments={"destination": "Shadowmere"},
    )
    action, policy_name, reason = resolve_egress_governance(
        intent,
        profile=GovPolicyProfile.BLOCK_DESTRUCTIVE,
        block_destructive=True,
        policy_engine=engine,
    )
    assert action == GovernanceAction.BLOCK
    assert policy_name == "block-destructive"
    assert "declarative" not in reason
    assert "destructive under the block-destructive policy" in reason


def test_blacklisted_tool_is_skipped() -> None:
    engine = GovernancePolicyEngine.from_yaml({"policies": []})
    engine.tool_blacklist.add("dangerous_tool")
    action, policy_name, reason = resolve_egress_governance(
        _intent(tool_name="dangerous_tool"),
        profile=GovPolicyProfile.PERMISSIVE,
        block_destructive=False,
        policy_engine=engine,
    )
    assert action == GovernanceAction.SKIP
    assert "blacklisted" in reason


# --- egress_from_config: hitl_on_tool short-circuit ----------------------


def test_hitl_on_tool_flag_produces_its_own_reason_regression() -> None:
    """Regression: describe_egress_reason had no hitl_on_tool parameter at
    all, so when this flag produced an HITL decision, the reported reason
    described an unrelated profile scenario instead. Now the decision and its
    reason are returned together by the same branch, so they cannot diverge."""
    config = KernelConfig(hitl_on_tool=True, gov_policy_profile=GovPolicyProfile.BLOCK_DESTRUCTIVE)
    decision, policy_name, reason = egress_from_config(_intent(destructive=False), config=config)
    assert decision.value == "HITL"
    assert "hitl_on_tool" in reason


def test_evaluate_egress_at_chokepoint_hitl_gov_override() -> None:
    config = KernelConfig()
    decision, policy_name, reason = evaluate_egress_at_chokepoint(
        _intent(destructive=True),
        config=config,
        hitl_gov_override=True,
    )
    assert decision.value == "ALLOW"
    assert policy_name == "hitl-override"
    assert "human approval" in reason


# --- ingress_governance_outcome ---------------------------------------------


def test_ingress_permissive_always_allows() -> None:
    action, reason = ingress_governance_outcome(response_kind="ERROR", profile=GovIngressProfile.PERMISSIVE)
    assert action == GovernanceAction.ALLOW
    assert "permissive" in reason


def test_ingress_non_error_always_allows() -> None:
    for profile in (
        GovIngressProfile.RETRY_ON_ERROR,
        GovIngressProfile.BLOCK_ON_ERROR,
        GovIngressProfile.SKIP_ON_ERROR,
    ):
        action, reason = ingress_governance_outcome(response_kind="TOOL_RESULT", profile=profile)
        assert action == GovernanceAction.ALLOW, profile
        assert "not an error" in reason, profile


def test_ingress_error_profiles() -> None:
    for profile, expected in (
        (GovIngressProfile.RETRY_ON_ERROR, GovernanceAction.RETRY),
        (GovIngressProfile.BLOCK_ON_ERROR, GovernanceAction.BLOCK),
        (GovIngressProfile.SKIP_ON_ERROR, GovernanceAction.SKIP),
    ):
        action, reason = ingress_governance_outcome(response_kind="ERROR", profile=profile)
        assert action == expected, profile
        assert "engine returned an error" in reason, profile
