#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Block leftover send to a dest that is not this agent's ``delegates_to``."""

from __future__ import annotations

from mas.runtime.boundary.delegation.policy import (
    delegation_targets,
    parse_delegate_tool_name,
)
from mas.runtime.boundary.gov.policy import EgressIntentView
from mas.runtime.engine.tools import tool_entry_name
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.coupling import GovDecision

PLUGIN_ID = "gov_no_undeclared_peer"


def undeclared_peer_observation(dest: str, allowed: list[str]) -> str:
    """Text the model sees when leftover send names a dest not in ``delegates_to``."""
    names = ", ".join(allowed) if allowed else "(none)"
    return f"Peer {dest!r} is not in this agent's delegates_to. Do not send to it. Allowed peers: {names}."


def send_dest(intent: EgressIntentView) -> str | None:
    """Dest of a leftover ``delegate_to_*`` call. Else ``None``.

    A tool named ``send`` is a user tool, never the send path, so its ``target`` argument is not checked.
    """
    return parse_delegate_tool_name(intent.tool_name or "")


def allowed_peers(config: KernelConfig) -> list[str] | None:
    """This agent's ``delegates_to``. ``None`` when the kernel does not know its id: no lock then."""
    spec = getattr(config, "agent_spec", None)
    if not isinstance(spec, dict):
        spec = {}
    agent_id = str(getattr(config, "agent_id", "") or "").strip()
    if not agent_id:
        return None
    return delegation_targets({"spec": spec}, agent_id=agent_id)


def _user_tool_names(config: KernelConfig) -> set[str]:
    spec = getattr(config, "agent_spec", None)
    tools = spec.get("tools") if isinstance(spec, dict) else None
    return {name for item in tools or [] if (name := tool_entry_name(item))}


class NoUndeclaredPeerPlugin:
    """Egress BLOCK when leftover send names a dest not in ``delegates_to``.

    Other tool names PASS so the rest of the chain can run. Send itself does
    not refuse; this plugin is the lock, named by ``with-hardened``.
    """

    plugin_id = "gov_no_undeclared_peer@v1"

    def __init__(self, **_raw: object) -> None:
        pass

    def evaluate_egress(self, intent: EgressIntentView, *, config: KernelConfig):
        if intent.op != "TOOL_CALL" or not (intent.tool_name or "").strip():
            return (
                GovDecision.ALLOW,
                PLUGIN_ID,
                "not a tool call",
            )
        dest = send_dest(intent)
        if dest is not None and (intent.tool_name or "").strip() in _user_tool_names(config):
            dest = None  # the agent's own tool that happens to be called delegate_to_<x>
        if dest is None:
            return (
                GovDecision.ALLOW,
                PLUGIN_ID,
                "not leftover send",
            )
        allowed = allowed_peers(config)
        if allowed is None:
            return (
                GovDecision.ALLOW,
                PLUGIN_ID,
                "this kernel has no agent id, so there is no delegates_to to check",
            )
        if dest in allowed:
            return (
                GovDecision.ALLOW,
                PLUGIN_ID,
                f"peer {dest!r} is in this agent's delegates_to",
            )
        reason = undeclared_peer_observation(dest, allowed)
        return GovDecision.BLOCK, PLUGIN_ID, reason
