#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``identity-guard``: a governance plugin that protects the wallet from the model's own tool calls.

The identity tools (``identity_delegate``, ``identity_revoke``) change what an agent can do, so they are shut by
default: without this plugin's policy a call to one is BLOCK, and the tools themselves refuse to run when no guard has
decided about the call (see :mod:`~mas.library.standard.lib.identity.permits`), so an agent with no guard configured
cannot use them at all. The guard is the opposite of ``identity-gate``. The gate
passes a call that has no requirement and checks the identity a tool needs; the guard blocks the wallet's own tools
unless a policy allows them. The two answer different questions, so they are two small plugins and not one with a mode.

Config (a Control entry):

* ``allow``: a mapping from a protected tool to ``true`` (allow) or ``"hold"`` (ask a human first, through the
  kernel's human-in-the-loop: with none configured the call is blocked, never allowed). A tool not named is blocked.
* ``protect``: the tools the guard covers, by default ``identity_delegate`` and ``identity_revoke``. The read tools
  (``identity_list``, ``identity_describe``) change nothing and are not covered unless named here.

Anything else, and anything that is not a tool call, is ALLOW (the chain contract: a plugin that does not apply
passes). Every decision about a covered tool is one closed ``identity.gate`` event with the tool as its operation and a
value-free code: ``guard_allowed``, ``guard_hold`` (asked), ``guard_hold_approved`` (the kernel asked again once a
human said yes: one
``ok`` event), ``guard_denied`` or ``guard_hold_unavailable`` (no human in the loop, or approvals are remembered for the
turn with ``hitl_once_per_turn``: a hold could then pass without asking, so it blocks). A failure inside
the guard is BLOCK ``gate_error``.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping, Sequence
from typing import Any

from mas.library.standard.lib.identity import observe, permits
from mas.library.standard.lib.identity.holders import scope_of
from mas.library.standard.lib.identity.requirements import GateConfigError
from mas.runtime.boundary.gov.policy import EgressIntentView
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.coupling import GovDecision

PLUGIN_ID = "gov_identity_guard"
#: The tools the guard covers unless ``protect`` says otherwise.
DEFAULT_PROTECT = ("identity_delegate", "identity_revoke")
#: Every tool of the identity plugin: what ``protect`` and ``allow`` may name.
KNOWN_TOOLS = ("identity_list", "identity_describe", "identity_delegate", "identity_revoke")
_OUTCOMES = {GovDecision.ALLOW: "ok", GovDecision.BLOCK: "denied", GovDecision.HITL: "hold"}

_log = logging.getLogger(__name__)
_MAX_HELD = 256


class IdentityGuard:
    """Shut the wallet's tools until a policy opens them. See the module text."""

    plugin_id = "gov_identity_guard@v1"

    def __init__(self, allow: Mapping[str, Any] | None = None, protect: Sequence[str] = DEFAULT_PROTECT) -> None:
        if not isinstance(protect, (list, tuple)) or any(tool not in KNOWN_TOOLS for tool in protect):
            raise GateConfigError(f"protect is a list of {', '.join(KNOWN_TOOLS)}")
        policy = dict(allow or {})
        for tool, rule in policy.items():
            if tool not in KNOWN_TOOLS:
                raise GateConfigError(f"allow names an unknown tool {tool!r}; the tools are {', '.join(KNOWN_TOOLS)}")
            if rule is not True and rule != "hold":
                raise GateConfigError(f"allow.{tool} is true or 'hold'")
        self._protect = frozenset(protect)
        self._allow = policy
        self._lock = threading.Lock()
        self._held: set[tuple[str, str, Any]] = set()  # calls a hold was asked for, until the kernel asks again

    def __repr__(self) -> str:
        return f"IdentityGuard(protect={sorted(self._protect)!r}, allow={sorted(self._allow)!r})"

    def evaluate_egress(self, intent: EgressIntentView, *, config: KernelConfig) -> tuple[GovDecision, str, str]:
        try:
            tool = (intent.tool_name or "").strip() if intent.op == "TOOL_CALL" else ""
            if tool not in self._protect:
                return GovDecision.ALLOW, PLUGIN_ID, "not a protected tool"
            scope, agent = scope_of(config.agent_spec), str(config.agent_id or "")
            decision = self._decide(tool, config, (scope, agent, intent.correlation_id))
            self._publish(tool, config, str(intent.correlation_id), decision)
            if decision[0] is GovDecision.ALLOW and tool in permits.PERMIT_TOOLS:
                permits.grant(scope, agent, tool, intent.correlation_id)  # this call, once: never a held or denied one
            return decision
        except Exception as exc:  # the guard fails closed: a decision that cannot be made is a refusal
            _log.warning("identity guard: decision failed: %s", type(exc).__name__)
            return GovDecision.BLOCK, PLUGIN_ID, "gate_error"

    def _decide(self, tool: str, config: KernelConfig, call: tuple[str, str, Any]) -> tuple[GovDecision, str, str]:
        rule = self._allow.get(tool)
        if rule is True:
            return GovDecision.ALLOW, PLUGIN_ID, "guard_allowed"
        if rule == "hold":
            # a human's yes is remembered for the turn when ``hitl_once_per_turn`` is set: a later hold would pass
            # without asking anyone, so with it a hold cannot be honoured and the call is blocked
            if not config.hitl_on_tool or config.hitl_once_per_turn:
                return GovDecision.BLOCK, PLUGIN_ID, "guard_hold_unavailable"
            with self._lock:
                if call in self._held:  # the kernel asks again for the same call once a human approved it
                    self._held.discard(call)
                    return GovDecision.ALLOW, PLUGIN_ID, "guard_hold_approved"
                self._held.add(call)
                while len(self._held) > _MAX_HELD:
                    self._held.discard(next(iter(self._held)))
            return GovDecision.HITL, PLUGIN_ID, "guard_hold"
        return GovDecision.BLOCK, PLUGIN_ID, "guard_denied"

    @staticmethod
    def _publish(tool: str, config: KernelConfig, call: str, decision: tuple[GovDecision, str, str]) -> None:
        verdict, _plugin, reason = decision
        observe.emit(
            "identity.gate",
            actor=str(config.agent_id or ""),
            operation=f"{observe.TOOL_OPERATION}{tool}",
            call=call,
            outcome=_OUTCOMES[verdict],
            code=reason,
        )


__all__ = ["DEFAULT_PROTECT", "KNOWN_TOOLS", "PLUGIN_ID", "IdentityGuard"]
