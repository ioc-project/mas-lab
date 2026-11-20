#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""environment@v1 — observer that fires lab environment triggers.

Always ALLOW. With no port bound it is a no-op. Exceptions from the port
are recorded and never raised into governance. No kernel-facing triggers
means a filter that matches nothing: an empty list would subscribe the
plugin to every transition at the driver.
"""

from __future__ import annotations

from typing import Mapping

from mas.library.standard.lib.environment_port import get_environment_port
from mas.library.standard.lib.triggers import Trigger
from mas.runtime.boundary.control.contract import ControlCapability
from mas.runtime.boundary.gov.filter import GovTransitionFilter
from mas.runtime.boundary.gov.policy import EgressIntentView
from mas.runtime.boundary.gov.transition import GovTransition
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.coupling import GovDecision

PLUGIN_ID = "environment"

# Driver: empty transition_filters() means everything. A plugin that should
# see nobody must return a filter that matches no real transition.
_NOBODY = GovTransitionFilter(hook="ingress", kind=("ENVIRONMENT_NONE",))

_ENV_METHODS = frozenset(
    {
        "pause",
        "resume",
        "steer",
        "enqueue_input",
        "snapshot",
        "persist",
        "cancel_inflight",
        "disable_tool",
        "enable_tool",
        "inspect",
        "list_checkpoints",
    }
)


def _on_for(transition: GovTransition) -> str:
    if transition.hook == "egress" and transition.op == "TOOL_CALL":
        return "tool_call"
    if transition.hook == "ingress" and transition.response_kind == "TOOL_RESULT":
        return "tool_result"
    if transition.hook == "egress" and transition.op == "LLM_CALL":
        return "llm_call"
    if transition.hook == "ingress" and transition.kind == "USER_INPUT_RECEIVED":
        return "turn_start"
    if transition.hook == "ingress" and transition.response_kind == "MODEL_TEXT":
        return "turn_end"
    return ""


def _tool_name(transition: GovTransition) -> str:
    attrs = transition.attributes if isinstance(transition.attributes, Mapping) else {}
    return str(attrs.get("tool_name") or attrs.get("name") or "").strip()


def _filters_for(triggers: tuple[Trigger, ...]) -> list[GovTransitionFilter]:
    needed: set[str] = {trigger.when.on for trigger in triggers}
    filters: list[GovTransitionFilter] = []
    if "tool_call" in needed:
        filters.append(GovTransitionFilter(hook="egress", op=("TOOL_CALL",)))
    if "tool_result" in needed:
        filters.append(GovTransitionFilter(hook="ingress", response_kind=("TOOL_RESULT",)))
    if "llm_call" in needed:
        filters.append(GovTransitionFilter(hook="egress", op=("LLM_CALL",)))
    if "turn_start" in needed:
        filters.append(GovTransitionFilter(hook="ingress", kind=("USER_INPUT_RECEIVED",)))
    if "turn_end" in needed:
        filters.append(GovTransitionFilter(hook="ingress", response_kind=("MODEL_TEXT",)))
    return filters


def _session_id(session: object) -> str:
    try:
        return str(session.session_id or "")
    except AttributeError:
        return ""


def _entry_agent(session: object) -> str:
    try:
        return str(session.entry_agent_id or "")
    except AttributeError:
        return ""


class EnvironmentPlugin:
    """Observer: match triggers on the bound port. Never decides BLOCK."""

    plugin_id = "environment@v1"

    def __init__(self, **_raw: object) -> None:
        del _raw

    def bind_session(self, session: object, *, control: object = None, manager: object = None) -> None:
        port = get_environment_port()
        if port is None:
            return
        session_id = _session_id(session)
        handle = control
        if manager is not None:
            try:
                make = manager.control
            except AttributeError:
                make = None
            if make is not None:
                handle = make(
                    capability=ControlCapability(
                        actor="environment",
                        surface="plugin",
                        session_ids=frozenset({session_id}) if session_id else frozenset(),
                        methods=_ENV_METHODS,
                    )
                )
        port.bind_control(handle, session_id=session_id, entry_agent=_entry_agent(session))

    def transition_filters(self) -> list[GovTransitionFilter]:
        port = get_environment_port()
        if port is None:
            return [_NOBODY]
        filters = _filters_for(port.triggers())
        return filters if filters else [_NOBODY]

    def evaluate_egress(self, intent: EgressIntentView, *, config: KernelConfig):
        del intent, config
        return (
            GovDecision.ALLOW,
            PLUGIN_ID,
            "environment observes; it does not block",
        )

    def on_transition(self, transition: object) -> None:
        try:
            self._handle(transition)
        except Exception as exc:  # noqa: BLE001 — observer must never fail closed
            port = get_environment_port()
            if port is not None:
                try:
                    port.note_error(f"{type(exc).__name__}")
                except Exception:
                    return

    def _handle(self, transition: object) -> None:
        if not isinstance(transition, GovTransition):
            return
        port = get_environment_port()
        if port is None:
            return
        on = _on_for(transition)
        if not on:
            return
        port.observe(
            on,
            tool=_tool_name(transition),
            agent=str(transition.agent_id or ""),
            hook=str(transition.hook or ""),
            op=str(transition.op or transition.response_kind or ""),
        )
