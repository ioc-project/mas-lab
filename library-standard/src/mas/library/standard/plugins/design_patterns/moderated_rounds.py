#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Moderated rounds — group negotiation, written as a :mod:`.state_machine` table.

``moderated_rounds`` is a preset: its params (``decider``, ``order``, ...) fill
in the state table below, and the generic state machine runs it. The entry
agent runs no LLM. Code decides who speaks, in which order, and when the group
is asked to confirm; agents decide only what they say:

* ``round``: the request goes to each speaker of ``order``, one at a time, with
  the public transcript so far. Then ``decide``.
* ``decide``: the ``decider`` answers with one JSON action: ``continue`` (with
  the next request), ``propose`` (with a plan), ``withhold`` or ``escalate``
  (with a reason).
* ``confirm``: a proposed plan goes to every confirmer. Each answers confirm or
  reject; nobody sees another's answer. All confirm: committed. Otherwise the
  next round asks the group to respond to the objections.
* ``record`` (only with ``record_tool``) passes the outcome to that tool; ``done``
  returns it to the user as JSON.

An unreadable reply is asked once more; a second one ends the run with reason
``protocol_error``, so a guess never decides the outcome.

To change the protocol (another set of actions, a vote instead of unanimity, a
ReAct step), write the table yourself with ``design_pattern: state_machine``.
"""

from __future__ import annotations

import re
from typing import Any

from mas.runtime.kernel.config import KernelConfig

from .state_machine import StateMachinePlugin

_PLACEHOLDER = re.compile(r"\{([A-Za-z_][\w.]*)(?:\|[^}]*)?\}")

DEFAULT_DECISION_INSTRUCTIONS = (
    "You have heard from each party in this round. Decide what to do next and answer with exactly one action:\n"
    '- continue: the negotiation is not finished. Put in "message" what you say to the group next.\n'
    '- propose: you are ready to put one complete plan to all parties for confirmation. Put the plan in "plan".\n'
    '- withhold: you will not commit to any plan. Say why in "reason".\n'
    '- escalate: this needs a human decision. Say why in "reason".'
)
DEFAULT_CONFIRM_QUESTION = "Do you confirm this plan version?"
DEFAULT_REJECTED_REQUEST = (
    "Plan version {version} did not get everyone's confirmation. The objections are in the "
    "transcript. Please respond so that we can reach a plan that all of us can confirm."
)
DEFAULT_CONTINUE_FALLBACK = "Please continue the negotiation."
DEFAULT_MAX_ROUNDS = 6
ACTIONS = ["continue", "propose", "withhold", "escalate"]


def build_table(params: dict[str, Any], participants: list[str]) -> dict[str, Any]:
    """The state table for ``params``; raises ValueError when the setup cannot run."""
    decider = str(params.get("decider") or "").strip()
    if not decider:
        raise ValueError("moderated_rounds needs params.decider (the agent that decides after each round).")
    for key in ("order", "confirmers", "hide_from_confirmers"):
        if params.get(key) is not None and not isinstance(params[key], list):
            raise ValueError(f"moderated_rounds params.{key} must be a list.")
    speakers = [str(p) for p in (params.get("order") or [p for p in participants if p != decider])]
    confirmers = [str(p) for p in (params.get("confirmers") or [decider, *speakers])]
    record_tool = str(params.get("record_tool") or "")
    after = "record" if record_tool else "done"

    def value(key: str, default: Any) -> Any:
        found = params.get(key)
        return default if found in (None, "", []) else found

    max_rounds = int(value("max_rounds", DEFAULT_MAX_ROUNDS)) or DEFAULT_MAX_ROUNDS

    def ends(
        status: str, reason: str, detail: str, *, when: dict[str, Any] | None = None, **updates: Any
    ) -> dict[str, Any]:
        """A transition that records how the negotiation ended."""
        row: dict[str, Any] = {"set": {"status": status, "reason": reason, "detail": detail, **updates}, "goto": after}
        if when:
            row["when"] = when
        return row

    def unreadable(**updates: Any) -> dict[str, Any]:
        return ends("withheld", "protocol_error", "{out.detail}", when={"out.ok": False}, **updates)

    def limit(extra_when: dict[str, Any] | None = None, **updates: Any) -> dict[str, Any]:
        when = {**(extra_when or {}), "round": {"gte": "{max_rounds}"}}
        return ends("withheld", "round_limit", "round limit of {max_rounds} reached", when=when, **updates)

    outcome = {
        "status": "{status}",
        "reason": "{reason}",
        "detail": "{detail}",
        "plan_version": "{plan_version}",
        "plan": "{final_plan}",
        "confirmations": "{confirmations}",
        "rounds": "{round}",
        "rejected_replies": "{rejected_replies}",
    }
    confirmations = {
        "extend": "{out.replies}",
        "as": {
            "party": "{item.agent}",
            "plan_version": "{plan_version}",
            "decision": "{item.word}",
            "reason": "{item.reason}",
        },
    }
    rejected = {"confirmations": confirmations, "rejected_replies": {"add": "{out.counts.reject}"}}
    # ``{version}`` in ``rejected_request`` is the plan version that was rejected.
    rejected_request = str(value("rejected_request", DEFAULT_REJECTED_REQUEST))
    other = sorted({name for name in _PLACEHOLDER.findall(rejected_request) if name != "version"})
    if other:
        raise ValueError(f"moderated_rounds params.rejected_request can only use {{version}}, not {other}.")
    rejected_request = rejected_request.replace("{version}", "{plan_version}")
    states: dict[str, Any] = {
        "round": {
            "run": "delegate",
            "agents": "{order}",
            "input": "{request|with_transcript}",
            "log": "each",
            "log_from": "{sender}",
            "log_body": "{request}",
            "on": [unreadable(), {"goto": "decide"}],
        },
        "decide": {
            "run": "delegate",
            "agents": ["{decider}"],
            "input": "Public transcript so far:\n{transcript}\n\n{decision_instructions}",
            "read": {
                "json": {
                    "fields": {
                        "action": {"enum": ACTIONS, "description": "what to do next"},
                        "message": {
                            "type": "string",
                            "optional": True,
                            "description": "what you say to the group next (action continue)",
                        },
                        "plan": {
                            "type": "object",
                            "required_if": {"action": "propose"},
                            "description": "the complete plan (action propose)",
                        },
                        "reason": {
                            "type": "string",
                            "optional": True,
                            "description": "why (actions withhold and escalate)",
                        },
                    }
                }
            },
            "retry": 1,
            "error_detail": "the decider gave no readable action twice",
            "on": [
                unreadable(),
                ends("withheld", "decider_decision", "{out.reason|or:no_reason}", when={"out.action": "withhold"}),
                ends("escalated", "decider_decision", "{out.reason|or:no_reason}", when={"out.action": "escalate"}),
                limit({"out.action": "continue"}),
                {
                    "when": {"out.action": "continue"},
                    "set": {
                        "request": "{out.message|or:continue_fallback}",
                        "sender": "{decider}",
                        "round": {"add": 1},
                    },
                    "goto": "round",
                },
                {
                    "when": {"out.action": "propose"},
                    "set": {
                        "plan_version": {"add": 1},
                        "plan": "{out.plan}",
                        "request": ("Plan version {plan_version}:\n{plan|without:hidden|json}\n\n{confirm_question}"),
                    },
                    "goto": "confirm",
                },
            ],
        },
        "confirm": {
            "run": "delegate",
            "agents": "{confirmers}",
            "input": "{request|with_transcript}",
            "read": {"word": ["confirm", "reject"]},
            "retry": 1,
            "error_detail": "{agent} gave no confirm or reject twice",
            "log": "after_all",
            "log_from": "{decider}",
            "log_body": "{request}",
            "log_reply": "{item.word}: {item.reason}",
            "on": [
                unreadable(confirmations=confirmations),  # keep the confirmations that did arrive
                ends(
                    "committed",
                    "decider_decision",
                    "",
                    when={"out.counts.reject": 0},
                    confirmations=confirmations,
                    final_plan="{plan}",
                ),
                limit(**rejected),
                {
                    "set": {**rejected, "request": rejected_request, "sender": "{decider}", "round": {"add": 1}},
                    "goto": "round",
                },
            ],
        },
        "done": {"run": "end", "result": {**outcome, "transcript": "{transcript_rows}"}},
    }
    if record_tool:
        states["record"] = {"run": "tool", "tool": record_tool, "args": dict(outcome), "on": [{"goto": "done"}]}
    return {
        "start": "round",
        "max_steps": 10 * max_rounds + 20,
        "vars": {
            # Texts and names from the params are kept as written: braces in them are not placeholders.
            "decider": {"literal": decider},
            "order": {"literal": speakers},
            "confirmers": {"literal": confirmers},
            "max_rounds": max_rounds,
            "hidden": {"literal": [str(h) for h in params.get("hide_from_confirmers") or []]},
            "request": "{task}",
            "sender": {"literal": str(params.get("opening_from") or decider)},
            "round": 1,
            "plan_version": 0,
            "plan": None,
            "final_plan": None,
            "confirmations": [],
            "rejected_replies": 0,
            "status": "",
            "reason": "",
            "detail": "",
            "no_reason": "",
            "decision_instructions": {"literal": str(value("decision_instructions", DEFAULT_DECISION_INSTRUCTIONS))},
            "confirm_question": {"literal": str(value("confirm_question", DEFAULT_CONFIRM_QUESTION))},
            "continue_fallback": {"literal": str(value("continue_fallback", DEFAULT_CONTINUE_FALLBACK))},
        },
        "states": states,
    }


class ModeratedRoundsPlugin(StateMachinePlugin):
    """Fixed-order rounds, a decider's action after each, and a confirmation round for every proposal."""

    plugin_id = "moderated_rounds@v1"
    mode = "moderated_rounds"
    params_are_templates = False  # its params are plain values, filled in from the parent's variables
    # Texts the parent leaves as written: they may hold braces, and ``{version}`` is the preset's own.
    raw_params = frozenset({"decision_instructions", "confirm_question", "continue_fallback", "rejected_request"})

    def definition(self, config: KernelConfig, participants: list[str]) -> dict[str, Any]:
        spec = getattr(config, "agent_spec", None) or {}
        binding = spec.get("design_pattern") if isinstance(spec, dict) else None
        params = binding.get("params") if isinstance(binding, dict) else None
        return build_table(dict(params) if isinstance(params, dict) else {}, participants)
