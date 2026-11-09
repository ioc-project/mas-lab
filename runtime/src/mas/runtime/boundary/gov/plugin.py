#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Governance plugin contract — M_gov asks; plugin replies; Mealy applies coupling."""

from __future__ import annotations

from typing import Protocol

from mas.runtime.boundary.gov.policy import EgressIntentView, resolve_egress_governance
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.coupling import GovDecision

_DENY = {
    GovDecision.BLOCK,
    GovDecision.SKIP,
    GovDecision.TERMINATE,
    GovDecision.BLACKLIST,
}
_HOLD = {GovDecision.HITL, GovDecision.RETRY, GovDecision.MODIFY}

# (decision, policy_name, human-readable reason) — computed together so the
# reason can never describe a different decision than the one returned
# alongside it. See boundary/gov/policy.py's module docstring for why this
# replaced a separate after-the-fact reconstruction.
EgressDecision = tuple[GovDecision, str, str]


class GovernancePlugin(Protocol):
    """Evaluate egress at chokepoint (policy/HITL rules live in plugins, not in M_tool/M_dp)."""

    def evaluate_egress(self, intent: EgressIntentView, *, config: KernelConfig) -> EgressDecision: ...


class GovernancePluginChain:
    """``spec.governance`` is an iptables-style chain, not a sequence.

    The Control set is unordered. Plugins that attach to the same transition
    run in ``priority`` order (higher first, default 0; same priority is the
    plugin id). Callers pass that order in. Observability plugins are a
    *sequence*: every listed plugin sees every event. Governance plugins are
    a *chain*: each plugin either passes and the next rule runs, or it errors
    and the chain stops with that verdict.
    Remaining plugins are not consulted.

    * ALLOW / LOG — pass. Continue to the next plugin.
    * BLOCK / SKIP / TERMINATE / BLACKLIST — error. Exit the chain and
      return that verdict.
    * HITL / RETRY / MODIFY — hold. Not a pass and not an error; keep
      walking so a later plugin can still BLOCK. If the chain ends without
      an error, the first hold is the verdict.

    A plugin that does not apply to this call MUST pass (ALLOW) so later
    rules can run.

    ``on_transition`` fans out to each child (optional hook). This plugin
    itself declares no ``transition_filters``, so the driver delivers the
    full stream and each child applies its own filters.
    """

    def __init__(self, plugins: list[GovernancePlugin]) -> None:
        self._plugins = list(plugins)

    @property
    def plugins(self) -> tuple[GovernancePlugin, ...]:
        return tuple(self._plugins)

    def evaluate_egress(self, intent: EgressIntentView, *, config: KernelConfig) -> EgressDecision:
        hold: EgressDecision | None = None
        log: EgressDecision | None = None
        allow: EgressDecision | None = None
        for plugin in self._plugins:
            decision, name, reason = plugin.evaluate_egress(intent, config=config)
            if decision in _DENY:
                return decision, name, reason
            if decision in _HOLD and hold is None:
                hold = (decision, name, reason)
            elif decision == GovDecision.LOG:
                log = (decision, name, reason)
            else:
                allow = (decision, name, reason)
        return (
            hold
            or log
            or allow
            or (
                GovDecision.ALLOW,
                "governance-chain",
                "all governance plugins passed this call",
            )
        )

    def on_transition(self, transition: object) -> None:
        for plugin in self._plugins:
            hook = getattr(plugin, "on_transition", None)
            if not callable(hook):
                continue
            filters_fn = getattr(plugin, "transition_filters", None)
            if callable(filters_fn):
                specs = list(filters_fn() or [])
                if specs:
                    matches = getattr(specs[0], "matches", None)
                    if callable(matches) and not any(
                        getattr(spec, "matches", lambda _t: False)(transition) for spec in specs
                    ):
                        continue
            hook(transition)


# Historical name — same chain object.
CompositeGovernancePlugin = GovernancePluginChain


def egress_from_config(intent: EgressIntentView, *, config: KernelConfig) -> EgressDecision:
    """Parametric default: ``gov_policy_profile`` / ``hitl_on_tool``. No plugin class."""
    if config.hitl_on_tool and intent.op == "TOOL_CALL":
        return (
            GovDecision.HITL,
            "hitl-on-tool",
            "the hitl_on_tool flag requires human review for every tool call",
        )
    action, policy_name, reason = resolve_egress_governance(
        intent,
        profile=config.gov_policy_profile,
        block_destructive=config.gov_block_destructive,
        policy_engine=config.policy_engine,
    )
    return GovDecision(action.value), policy_name, reason


def evaluate_egress_at_chokepoint(
    intent: EgressIntentView,
    *,
    config: KernelConfig,
    plugin: GovernancePlugin | None = None,
    hitl_gov_override: bool = False,
) -> EgressDecision:
    """Return the ``(decision, policy_name, reason)`` egress verdict for one
    call. A prior human approval (``hitl_gov_override``) short-circuits to
    ALLOW before ``plugin`` (or ``config.egress_governance_plugin``) is
    consulted; otherwise the parametric profile applies.
    """
    if hitl_gov_override:
        return GovDecision.ALLOW, "hitl-override", "a prior human approval covers this call"
    impl = plugin or getattr(config, "egress_governance_plugin", None)
    if impl is None:
        return egress_from_config(intent, config=config)
    return impl.evaluate_egress(intent, config=config)
