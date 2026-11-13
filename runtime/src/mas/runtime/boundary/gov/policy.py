#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Egress governance — parametric policy profiles aligned to TLA Governance.tla."""

from __future__ import annotations

from dataclasses import dataclass, replace

from mas.runtime.boundary.connection import ConnectionInfo
from mas.runtime.schema.governance import (
    GovernanceAction,
    GovIngressProfile,
    GovPolicyProfile,
)


@dataclass(frozen=True)
class EgressIntentView:
    op: str
    destructive: bool
    correlation_id: int
    tool_name: str = ""
    tool_arguments: dict | None = None
    # Names in the OpenAI ``tools`` array of the LLM call that produced this
    # TOOL_CALL. None = not recorded (fall back to spec.tools). () = recorded
    # as an empty offer — the model was given no tools this call.
    offered_tools: tuple[str, ...] | None = None
    connection: ConnectionInfo | None = None


_PROFILE_LABEL: dict[GovPolicyProfile, str] = {
    GovPolicyProfile.PERMISSIVE: "permissive",
    GovPolicyProfile.BLOCK_DESTRUCTIVE: "block-destructive",
    GovPolicyProfile.HITL_DESTRUCTIVE: "hitl-destructive",
    GovPolicyProfile.LOG_ALL: "log-all",
    GovPolicyProfile.MODIFY_DESTRUCTIVE: "modify-destructive",
    GovPolicyProfile.TERMINATE_DESTRUCTIVE: "terminate-destructive",
    GovPolicyProfile.SKIP_DESTRUCTIVE: "skip-destructive",
    GovPolicyProfile.BLACKLIST_DESTRUCTIVE: "blacklist-destructive",
    GovPolicyProfile.RETRY_DESTRUCTIVE: "retry-destructive",
}


def resolve_egress_governance(
    intent: EgressIntentView,
    *,
    profile: GovPolicyProfile,
    block_destructive: bool,
    policy_engine: object | None = None,
) -> tuple[GovernanceAction, str, str]:
    """Return ``(decision, policy_name, reason)`` in one pass.

    The explanation is computed alongside the decision, not reconstructed
    from the same inputs afterward — a separate reconstruction can (and did)
    fall out of sync with branches added here, e.g. missing a short-circuit
    the caller applies before ever reaching this function. A prior human
    approval (``hitl_gov_override``) is handled entirely by the chokepoint
    caller, which returns ALLOW before this function is ever invoked — it is
    not a case this function needs to know about.
    """
    if policy_engine is not None and intent.op == "TOOL_CALL" and intent.tool_name:
        from mas.runtime.boundary.gov.policy_engine import GovernancePolicyEngine

        assert isinstance(policy_engine, GovernancePolicyEngine)
        if policy_engine.is_blacklisted(intent.tool_name):
            return (
                GovernanceAction.SKIP,
                "declarative",
                f"tool '{intent.tool_name}' is blacklisted",
            )
        policy = policy_engine.find_matching_policy(
            "tool_input",
            {"arguments": intent.tool_arguments or {}},
            tool_name=intent.tool_name,
        )
        if policy is not None:
            decision = policy_engine.map_action(policy)
            authored = policy.params.get("message") or policy.params.get("reason")
            reason = str(authored) if authored else f"policy '{policy.name}' triggered {decision.value}"
            return decision, policy.name, reason
    return egress_governance_outcome(
        intent,
        profile=profile,
        block_destructive=block_destructive,
    )


def egress_governance_outcome(
    intent: EgressIntentView,
    *,
    profile: GovPolicyProfile,
    block_destructive: bool,
) -> tuple[GovernanceAction, str, str]:
    """Return ``(decision, policy_name, reason)`` for the parametric-profile
    fallback path — no declarative policy matched, or none was configured."""
    label = _PROFILE_LABEL.get(profile, str(profile))
    if profile == GovPolicyProfile.PERMISSIVE:
        return GovernanceAction.ALLOW, label, "permissive profile allows every action"
    if profile == GovPolicyProfile.LOG_ALL:
        return GovernanceAction.LOG, label, "log-all profile allows the action and records it"

    action_for_destructive = {
        GovPolicyProfile.BLOCK_DESTRUCTIVE: GovernanceAction.BLOCK,
        GovPolicyProfile.HITL_DESTRUCTIVE: GovernanceAction.HITL,
        GovPolicyProfile.MODIFY_DESTRUCTIVE: GovernanceAction.MODIFY,
        GovPolicyProfile.TERMINATE_DESTRUCTIVE: GovernanceAction.TERMINATE,
        GovPolicyProfile.SKIP_DESTRUCTIVE: GovernanceAction.SKIP,
        GovPolicyProfile.BLACKLIST_DESTRUCTIVE: GovernanceAction.BLACKLIST,
        GovPolicyProfile.RETRY_DESTRUCTIVE: GovernanceAction.RETRY,
    }.get(profile)
    if action_for_destructive is None:
        return GovernanceAction.ALLOW, label, f"unrecognized profile {label} defaults to allow"
    if not intent.destructive:
        return (
            GovernanceAction.ALLOW,
            label,
            f"action is non-destructive; the {label} policy only acts on destructive calls",
        )
    # BLOCK_DESTRUCTIVE additionally requires block_destructive=True to act —
    # every other destructive-only profile always acts once destructive=True.
    if profile == GovPolicyProfile.BLOCK_DESTRUCTIVE and not block_destructive:
        return (
            GovernanceAction.ALLOW,
            label,
            f"action is destructive, but the {label} policy is configured with "
            "block_destructive=False so it only logs, not blocks",
        )
    return action_for_destructive, label, f"action is destructive under the {label} policy"


def ingress_governance_outcome(
    *,
    response_kind: str,
    profile: GovIngressProfile,
) -> tuple[GovernanceAction, str]:
    """Return ``(decision, reason)`` for an ingress (post-call) governance
    check — only ``response_kind == "ERROR"`` triggers anything beyond ALLOW."""
    if profile == GovIngressProfile.PERMISSIVE:
        return GovernanceAction.ALLOW, "permissive profile allows every engine response"
    if response_kind != "ERROR":
        return (
            GovernanceAction.ALLOW,
            f"response is not an error; {profile.value} profile only acts on errors",
        )
    action_and_verb = {
        GovIngressProfile.RETRY_ON_ERROR: (GovernanceAction.RETRY, "retries the call"),
        GovIngressProfile.BLOCK_ON_ERROR: (GovernanceAction.BLOCK, "blocks the call"),
        GovIngressProfile.SKIP_ON_ERROR: (GovernanceAction.SKIP, "skips the call"),
    }.get(profile)
    if action_and_verb is None:
        return GovernanceAction.ALLOW, f"unrecognized profile {profile.value} defaults to allow"
    action, verb = action_and_verb
    return action, f"engine returned an error; the {profile.value} policy {verb}"


def apply_egress_modify(intent: EgressIntentView, action: GovernanceAction) -> EgressIntentView:
    """Downgrade a MODIFY-decided intent to non-destructive; a no-op for any
    other action or an already-non-destructive intent."""
    if action == GovernanceAction.MODIFY and intent.destructive:
        return replace(intent, destructive=False)
    return intent
