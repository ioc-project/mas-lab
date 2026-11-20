#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Governance plugin contract — M_gov asks; plugin replies; Mealy applies coupling."""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from mas.runtime.boundary.gov.policy import EgressIntentView, resolve_egress_governance
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.coupling import GovDecision
from mas.runtime.schema.egress import EmitHitlRequest
from mas.runtime.schema.ingress import HitlResolve

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


class HitlResolveVerifier(Protocol):
    """Optional second hook: check a HITL answer before an ALLOW takes effect.

    A plugin that asked for a hold (``HITL``) may also want to check the answer
    (a signature, a token in ``resolve.operator_context``). It implements
    ``evaluate_hitl_resolve``; a plugin without it has no opinion.

    Return ``None`` for no opinion (or ``ALLOW``). Any deny verdict
    (``BLOCK``, ``SKIP``, ``TERMINATE``, ``BLACKLIST``) refuses the ALLOW and
    the kernel applies ``BLOCK`` instead. The reason must not carry the answer
    or any secret: it is written to the tool result and the trace.
    """

    def evaluate_hitl_resolve(self, request: EmitHitlRequest, resolve: HitlResolve) -> EgressDecision | None: ...


_RESOLVE_HOOK_FAILED: EgressDecision = (
    GovDecision.BLOCK,
    "hitl-resolve-hook-error",
    "the governance check of the approval failed, so the call is refused",
)


def _verify_with(plugin: object, request: EmitHitlRequest, resolve: HitlResolve) -> EgressDecision | None:
    hook = getattr(plugin, "evaluate_hitl_resolve", None)
    if not callable(hook):
        return None
    try:
        verdict = hook(request, resolve)
    except Exception:
        return _RESOLVE_HOOK_FAILED
    if verdict is None:
        return None
    try:
        decision, name, reason = verdict
        return (GovDecision(decision), str(name), str(reason)) if decision in _DENY else None
    except (TypeError, ValueError):
        return _RESOLVE_HOOK_FAILED


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
            if decision == GovDecision.HITL and intent.hitl_approved:
                decision, name, reason = GovDecision.ALLOW, "hitl-override", "a prior human approval covers this call"
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

    def evaluate_hitl_resolve(self, request: EmitHitlRequest, resolve: HitlResolve) -> EgressDecision | None:
        """First deny wins, in chain order, as for egress. An exception or a malformed verdict is a BLOCK."""
        for plugin in self._plugins:
            verdict = _verify_with(plugin, request, resolve)
            if verdict is not None:
                return verdict
        return None

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
    """Return the ``(decision, policy_name, reason)`` egress verdict for one call.

    The chain always runs. A prior human approval (``hitl_gov_override``) only
    turns a HITL hold into ALLOW for this call; a deny from any plugin still
    wins, and so does a hold of another kind (RETRY, MODIFY).
    """
    impl = plugin or getattr(config, "egress_governance_plugin", None)
    if hitl_gov_override:
        intent = replace(intent, hitl_approved=True)
    verdict = egress_from_config(intent, config=config) if impl is None else impl.evaluate_egress(intent, config=config)
    if hitl_gov_override and verdict[0] == GovDecision.HITL:
        return GovDecision.ALLOW, "hitl-override", "a prior human approval covers this call"
    return verdict


def verify_hitl_resolve(plugin: object | None, request: EmitHitlRequest, resolve: HitlResolve) -> EgressDecision | None:
    """Ask the governance plugin (or chain) to check a HITL answer. ``None`` means the ALLOW stands."""
    if plugin is None:
        return None
    return _verify_with(plugin, request, resolve)
