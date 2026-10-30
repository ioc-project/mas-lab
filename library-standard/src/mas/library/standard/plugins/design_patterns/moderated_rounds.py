#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Moderated rounds — a deterministic moderator for group negotiation.

The entry agent runs no LLM. Code decides who speaks, in which order, and when
the group is asked to confirm; agents decide only what they say:

1. ROUND: the request goes to each speaker of ``order``, one at a time. Each
   one gets the public transcript so far and the request.
2. DECIDE: the ``decider`` gets the transcript and ``decision_instructions``
   and answers with one JSON action: ``continue`` (with the next request),
   ``propose`` (with a plan), ``withhold`` or ``escalate`` (with a reason).
3. CONFIRM: a proposed plan goes to every ``confirmer``. Each answers confirm
   or reject from the transcript before the round; nobody sees another's
   answer. All confirm: committed. Otherwise the next round asks the group to
   respond to the objections.
4. RECORD: the outcome (status, reason, plan version, plan, confirmations) is
   passed to ``record_tool`` if one is set, then returned to the user as JSON.

A reply that the moderator cannot read (no JSON action, no confirm/reject) is
asked once more; a second unreadable reply ends the run with reason
``protocol_error``, so a guess never decides the outcome.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.egress_gate import schedule_tool_egress
from mas.runtime.kernel.state import QProduct, RunLedger
from mas.runtime.schema.egress import EgressSymbol

from .base import _DeterministicBase, _TurnState
from .utils import dispatch_single

ACTIONS = ("continue", "propose", "withhold", "escalate")

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
DECISION_FORMAT = (
    'Answer with ONLY a JSON object: {"action": "continue" | "propose" | "withhold" | "escalate", '
    '"message": "...", "plan": {...}, "reason": "..."}. No other text.'
)
CONFIRM_FORMAT = "Start your reply with the word confirm or the word reject, then give your reason."

_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```")


@dataclass
class _RoundsState(_TurnState):
    phase: str = "ROUND"
    speakers: list[str] = field(default_factory=list)
    decider: str = ""
    confirmers: list[str] = field(default_factory=list)
    opening_from: str = ""
    max_rounds: int = 6
    request: str = ""
    sender: str = ""
    transcript: list[dict[str, Any]] = field(default_factory=list)
    plan_version: int = 0
    plan: dict[str, Any] | None = None
    confirmations: list[dict[str, Any]] = field(default_factory=list)
    confirm_transcript: list[dict[str, Any]] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    awaiting: str = ""
    retried: bool = False
    rejected_replies: int = 0
    outcome: dict[str, Any] | None = None
    recorded: bool = False


def parse_decision(text: str) -> dict[str, Any] | None:
    """The decider's action as a dict, or None when the reply holds no readable action."""
    stripped = text.strip()
    fence = _FENCE.search(stripped)
    candidates = [fence.group(1).strip()] if fence else []
    candidates.append(stripped)
    brace = stripped.find("{")
    if brace >= 0:
        candidates.append(stripped[brace:])
    for candidate in candidates:
        try:
            value, _ = json.JSONDecoder().raw_decode(candidate)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("action") in ACTIONS:
            if value["action"] == "propose" and not isinstance(value.get("plan"), dict):
                return None
            return value
    return None


def parse_confirmation(text: str) -> tuple[str, str] | None:
    """``("confirm" | "reject", reason)`` from a reply that starts with that word, else None."""
    match = re.match(r"^\W*(confirm|reject)\w*\b[\s:.,\-*_]*(.*)$", text.strip(), re.I | re.S)
    if not match:
        return None
    return match.group(1).lower(), match.group(2).strip()


def render_transcript(transcript: list[dict[str, Any]]) -> str:
    """``[seq] from -> to: body`` lines, the public transcript every party sees."""
    return "\n".join(
        f"[{entry['seq']}] {entry['from']} -> {', '.join(entry['to'])}: {entry['body']}" for entry in transcript
    )


def render_plan(plan: dict[str, Any], version: int, hidden: list[str]) -> str:
    """The plan as the confirmers are asked about it: only what was proposed, minus ``hidden`` fields."""

    def strip(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if k not in hidden}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value

    return f"Plan version {version}:\n{json.dumps(strip(plan), indent=2, sort_keys=False)}"


class ModeratedRoundsPlugin(_DeterministicBase):
    """Fixed-order rounds, a decider's action after each, and a confirmation round for every proposal."""

    plugin_id = "moderated_rounds@v1"
    mode = "moderated_rounds"

    def _make_state(self) -> _RoundsState:
        return _RoundsState()

    def _state(self) -> _RoundsState:  # type: ignore[override]
        return self._turn_state  # type: ignore[return-value]

    # --- configuration ---

    @staticmethod
    def _params(config: KernelConfig) -> dict[str, Any]:
        spec = getattr(config, "agent_spec", None) or {}
        binding = spec.get("design_pattern") if isinstance(spec, dict) else None
        params = binding.get("params") if isinstance(binding, dict) else None
        return dict(params) if isinstance(params, dict) else {}

    def _configure(self, st: _RoundsState, config: KernelConfig) -> str | None:
        """Fill the turn state from params; return an error message when the setup cannot run."""
        params = self._params(config)
        participants = list(st.participants)
        st.decider = str(params.get("decider") or "").strip()
        if not st.decider:
            return "moderated_rounds needs params.decider (the agent that decides after each round)."
        st.speakers = [str(p) for p in (params.get("order") or [p for p in participants if p != st.decider])]
        st.confirmers = [str(p) for p in (params.get("confirmers") or [st.decider, *st.speakers])]
        unknown = sorted({st.decider, *st.speakers, *st.confirmers} - set(participants))
        if unknown:
            return f"moderated_rounds: {unknown} are not agents this moderator can delegate to ({participants})."
        st.max_rounds = int(params.get("max_rounds") or 6)
        st.opening_from = str(params.get("opening_from") or st.decider)
        st.request = st.original_task
        st.sender = st.opening_from
        return None

    def _param(self, config: KernelConfig, key: str, default: Any) -> Any:
        value = self._params(config).get(key)
        return default if value in (None, "", []) else value

    # --- transcript ---

    def _log(self, st: _RoundsState, sender: str, to: list[str], body: str) -> None:
        st.transcript.append({"seq": len(st.transcript) + 1, "from": sender, "to": list(to), "body": body})

    def _with_transcript(self, st: _RoundsState, request: str, transcript: list[dict[str, Any]] | None = None) -> str:
        rows = st.transcript if transcript is None else transcript
        if not rows:
            return request
        return f"Public transcript so far:\n{render_transcript(rows)}\n\nRequest:\n{request}"

    # --- the state machine ---

    def evaluate_next(self, q: QProduct, run: RunLedger, *, config: KernelConfig) -> list[EgressSymbol]:
        st = self._state()
        if not st.participants:
            return self._no_participants(q)
        if st.phase == "ROUND" and not st.speakers and not st.awaiting:
            error = self._configure(st, config)
            if error:
                return self._finish(q, error)
            return self._begin_round(q, run, config)

        if st.phase == "RECORD":
            # The record tool has answered (or failed); the outcome stands either way.
            return self._finish(q, json.dumps(st.outcome, indent=2))
        latest = run.latest()
        reply = latest.text.strip() if latest is not None and latest.response_kind == "TOOL_RESULT" else None
        if latest is not None and latest.response_kind == "ERROR":
            return self._end(
                q, run, config, "withheld", "protocol_error", f"{st.awaiting} failed: {latest.text.strip()}"
            )
        if reply is None:
            return self._end(q, run, config, "withheld", "protocol_error", f"no reply from {st.awaiting}")
        if st.phase == "ROUND":
            return self._on_round_reply(q, run, config, reply)
        if st.phase == "DECIDE":
            return self._on_decision(q, run, config, reply)
        if st.phase == "CONFIRM":
            return self._on_confirmation(q, run, config, reply)
        return self._finish(q, "moderated_rounds: unknown phase")

    def _begin_round(self, q: QProduct, run: RunLedger, config: KernelConfig) -> list[EgressSymbol]:
        st = self._state()
        if st.round_num > st.max_rounds:
            return self._end(q, run, config, "withheld", "round_limit", f"round limit of {st.max_rounds} reached")
        st.phase = "ROUND"
        st.pending = list(st.speakers)
        return self._next_speaker(q, run, config)

    def _next_speaker(self, q: QProduct, run: RunLedger, config: KernelConfig) -> list[EgressSymbol]:
        st = self._state()
        party = st.pending.pop(0)
        st.awaiting = party
        task = self._with_transcript(st, st.request)
        self._log(st, st.sender, [party], st.request)
        return dispatch_single(self, q, run, config, task, party)

    def _on_round_reply(self, q: QProduct, run: RunLedger, config: KernelConfig, reply: str) -> list[EgressSymbol]:
        st = self._state()
        self._log(st, st.awaiting, [st.sender], reply)
        if st.pending:
            return self._next_speaker(q, run, config)
        return self._ask_decider(q, run, config)

    def _ask_decider(
        self, q: QProduct, run: RunLedger, config: KernelConfig, *, again: bool = False
    ) -> list[EgressSymbol]:
        st = self._state()
        st.phase = "DECIDE"
        st.awaiting = st.decider
        instructions = str(self._param(config, "decision_instructions", DEFAULT_DECISION_INSTRUCTIONS))
        prefix = "Your last answer had no readable action. " if again else ""
        transcript = render_transcript(st.transcript)
        task = f"Public transcript so far:\n{transcript}\n\n{prefix}{instructions}\n\n{DECISION_FORMAT}"
        return dispatch_single(self, q, run, config, task, st.decider)

    def _on_decision(self, q: QProduct, run: RunLedger, config: KernelConfig, reply: str) -> list[EgressSymbol]:
        st = self._state()
        decision = parse_decision(reply)
        if decision is None:
            if not st.retried:
                st.retried = True
                return self._ask_decider(q, run, config, again=True)
            return self._end(q, run, config, "withheld", "protocol_error", "the decider gave no readable action twice")
        st.retried = False
        action = decision["action"]
        if action == "withhold":
            return self._end(q, run, config, "withheld", "decider_decision", str(decision.get("reason") or ""))
        if action == "escalate":
            return self._end(q, run, config, "escalated", "decider_decision", str(decision.get("reason") or ""))
        if action == "continue":
            st.request = str(decision.get("message") or "").strip() or str(
                self._param(config, "continue_fallback", DEFAULT_CONTINUE_FALLBACK)
            )
            st.sender = st.decider
            st.round_num += 1
            return self._begin_round(q, run, config)
        st.plan_version += 1
        st.plan = dict(decision["plan"])
        st.phase = "CONFIRM"
        st.pending = list(st.confirmers)
        st.confirm_transcript = list(st.transcript)
        hidden = [str(h) for h in self._param(config, "hide_from_confirmers", [])]
        question = str(self._param(config, "confirm_question", DEFAULT_CONFIRM_QUESTION))
        st.request = f"{render_plan(st.plan, st.plan_version, hidden)}\n\n{question} {CONFIRM_FORMAT}"
        return self._next_confirmer(q, run, config)

    def _next_confirmer(self, q: QProduct, run: RunLedger, config: KernelConfig) -> list[EgressSymbol]:
        st = self._state()
        party = st.pending[0]
        st.awaiting = party
        # Every confirmer sees the transcript as it stood when the plan was proposed.
        task = self._with_transcript(st, st.request, st.confirm_transcript)
        return dispatch_single(self, q, run, config, task, party)

    def _on_confirmation(self, q: QProduct, run: RunLedger, config: KernelConfig, reply: str) -> list[EgressSymbol]:
        st = self._state()
        party = st.awaiting
        answer = parse_confirmation(reply)
        if answer is None:
            if not st.retried:
                st.retried = True
                return self._next_confirmer(q, run, config)
            return self._end(q, run, config, "withheld", "protocol_error", f"{party} gave no confirm or reject twice")
        st.retried = False
        decision, reason = answer
        st.pending.pop(0)
        st.confirmations.append(
            {"party": party, "plan_version": st.plan_version, "decision": decision, "reason": reason}
        )
        if st.pending:
            return self._next_confirmer(q, run, config)
        # Answers become public only once every confirmer has answered.
        self._log(st, st.decider, list(st.confirmers), st.request)
        for row in st.confirmations:
            if row["plan_version"] == st.plan_version:
                self._log(st, row["party"], [st.decider], f"{row['decision']}: {row['reason']}".strip())
        rejected = [
            row for row in st.confirmations if row["plan_version"] == st.plan_version and row["decision"] == "reject"
        ]
        if not rejected:
            return self._end(q, run, config, "committed", "decider_decision", "")
        st.rejected_replies += len(rejected)
        template = str(self._param(config, "rejected_request", DEFAULT_REJECTED_REQUEST))
        st.request = template.format(version=st.plan_version)
        st.sender = st.decider
        st.round_num += 1
        return self._begin_round(q, run, config)

    def _end(
        self, q: QProduct, run: RunLedger, config: KernelConfig, status: str, reason: str, detail: str
    ) -> list[EgressSymbol]:
        st = self._state()
        st.phase = "RECORD"
        st.outcome = {
            "status": status,
            "reason": reason,
            "detail": detail,
            "plan_version": st.plan_version,
            "plan": st.plan if status == "committed" else None,
            "confirmations": list(st.confirmations),
            "rounds": min(st.round_num, st.max_rounds),
            "rejected_replies": st.rejected_replies,
            "transcript": list(st.transcript),
        }
        record_tool = str(self._param(config, "record_tool", "") or "")
        if not record_tool or st.recorded:
            return self._finish(q, json.dumps(st.outcome, indent=2))
        st.recorded = True
        st.awaiting = record_tool
        q.pending_tool_name = record_tool
        q.pending_tool_args = {k: v for k, v in st.outcome.items() if k != "transcript"}
        return schedule_tool_egress(q, run, config)
