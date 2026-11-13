#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""moderated_rounds: a whole turn against a scripted engine. No model."""

from __future__ import annotations

import json
from collections.abc import Callable

from mas.library.standard.plugins.design_patterns.moderated_rounds import (
    DEFAULT_REJECTED_REQUEST,
    ModeratedRoundsPlugin,
    build_table,
)
from mas.library.standard.plugins.design_patterns.state_machine import validate_definition
from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.registry import get_registry
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn

SPEAKERS = ["seller_a", "seller_b", "carrier"]


def _spec(**params: object) -> dict:
    return {
        "design_pattern": {"type": "moderated_rounds", "params": {"decider": "lead", **params}},
        "workflow": {
            "entry": "moderator",
            "nodes": [
                {"id": "moderator", "delegates_to": ["lead", *SPEAKERS]},
                *({"id": p} for p in ["lead", *SPEAKERS]),
            ],
        },
    }


class _Engine:
    """Answers every delegation with ``reply(agent, task)`` and records the calls in order."""

    def __init__(self, reply: Callable[[str, str], str]) -> None:
        self.reply = reply
        self.instance: RuntimeInstance | None = None
        self.calls: list[tuple[str, dict]] = []

    def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        assert io.op == "TOOL_CALL", f"moderated_rounds must not call the model, got {io.op}"
        q = self.instance.kernel.q
        name, args = q.pending_tool_name, dict(q.pending_tool_args)
        self.calls.append((name, args))
        agent = name.removeprefix("delegate_to_")
        text = self.reply(agent, str(args.get("task") or "")) if name.startswith("delegate_to_") else "recorded"
        return EngineIoReturn(
            correlation_id=io.correlation_id, response_kind="TOOL_RESULT", next_step="STOP", text=text
        )

    async def ainvoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        return self.invoke(io)


def _raw(reply: Callable[[str, str], str], **params: object) -> tuple[str, _Engine]:
    engine = _Engine(reply)
    instance = RuntimeInstance.from_parts(
        config=KernelConfig(agent_spec=_spec(**params), agent_id="moderator"),
        engine=engine,
        enable_observability=False,
        enable_coordination=False,
    )
    instance.kernel.plugin = ModeratedRoundsPlugin()
    engine.instance = instance
    trace = instance.run_user_text("We need 100 units by Friday.")
    assert trace.client_responses, "the turn produced no response"
    return trace.client_responses[-1].content, engine


def _run(reply: Callable[[str, str], str], **params: object) -> tuple[dict, _Engine]:
    text, engine = _raw(reply, **params)
    return json.loads(text), engine


def _decisions(*actions: dict) -> Callable[[str, str], str]:
    """The lead answers decide requests with ``actions`` in turn and confirms every plan."""
    queue = list(actions)

    def reply(agent: str, task: str) -> str:
        if agent == "lead" and '"action"' in task:
            return json.dumps(queue.pop(0))
        if "Do you confirm" in task:
            return "confirm: it fits my facts"
        return f"{agent} offers its part."

    return reply


PLAN = {"lots": [{"seller": "seller_a", "units": 60}, {"seller": "seller_b", "units": 40}], "claimed_eta": "Thu"}


def test_registered() -> None:
    info = get_registry().resolve("moderated_rounds")
    assert info is not None and info.class_name == "ModeratedRoundsPlugin"


def test_rounds_follow_the_fixed_order_then_commit() -> None:
    outcome, engine = _run(
        _decisions({"action": "continue", "message": "Can anyone ship faster?"}, {"action": "propose", "plan": PLAN})
    )
    order = [name.removeprefix("delegate_to_") for name, _ in engine.calls]
    assert order == [*SPEAKERS, "lead", *SPEAKERS, "lead", "lead", *SPEAKERS]
    assert outcome["status"] == "committed"
    assert outcome["plan_version"] == 1 and outcome["plan"] == PLAN
    assert [c["party"] for c in outcome["confirmations"]] == ["lead", *SPEAKERS]
    # Round two carries the decider's message and the public transcript of round one.
    second = engine.calls[4][1]["task"]
    assert "Can anyone ship faster?" in second and "seller_a -> moderator" not in second
    assert "[1] lead -> seller_a: We need 100 units by Friday." in second


def test_confirmers_see_the_plan_without_hidden_fields_and_not_each_other() -> None:
    _, engine = _run(_decisions({"action": "propose", "plan": PLAN}), hide_from_confirmers=["claimed_eta"])
    asks = [args["task"] for name, args in engine.calls if "Do you confirm" in args.get("task", "")]
    assert len(asks) == 4 and len(set(asks)) == 1
    assert "claimed_eta" not in asks[0] and "Plan version 1" in asks[0]


def test_a_rejection_reopens_the_negotiation_with_a_new_version() -> None:
    state = {"asked": 0}

    def reply(agent: str, task: str) -> str:
        if agent == "lead" and '"action"' in task:
            return json.dumps({"action": "propose", "plan": PLAN})
        if "Do you confirm" in task:
            state["asked"] += 1
            return "reject: too late" if agent == "carrier" and state["asked"] <= 4 else "confirm"
        return "ok"

    outcome, engine = _run(reply)
    assert outcome["status"] == "committed" and outcome["plan_version"] == 2
    assert outcome["rejected_replies"] == 1
    round_two = [args["task"] for name, args in engine.calls if name == "delegate_to_seller_a"][2]
    assert round_two.endswith("Request:\n" + DEFAULT_REJECTED_REQUEST.format(version=1))
    # The rejection and its reason are now public.
    assert "carrier -> lead: reject: too late" in round_two


def test_round_limit_withholds() -> None:
    outcome, engine = _run(_decisions(*[{"action": "continue", "message": "more"}] * 2), max_rounds=2)
    assert outcome["status"] == "withheld" and outcome["reason"] == "round_limit"
    assert outcome["plan"] is None


def test_withhold_and_escalate_end_the_run() -> None:
    assert _run(_decisions({"action": "withhold", "reason": "nothing fits"}))[0]["status"] == "withheld"
    assert _run(_decisions({"action": "escalate", "reason": "needs a human"}))[0]["status"] == "escalated"


def test_two_unreadable_decisions_are_a_protocol_error() -> None:
    outcome, engine = _run(lambda agent, task: "I think we should talk more." if agent == "lead" else "ok")
    assert outcome["reason"] == "protocol_error"
    assert [n for n, _ in engine.calls].count("delegate_to_lead") == 2


def test_record_tool_gets_the_outcome() -> None:
    outcome, engine = _run(_decisions({"action": "propose", "plan": PLAN}), record_tool="write_commitment")
    name, args = engine.calls[-1]
    assert name == "write_commitment"
    assert args["status"] == "committed" and args["plan"] == PLAN and len(args["confirmations"]) == 4
    assert "transcript" not in args and "transcript" in outcome


def test_unknown_participant_is_refused() -> None:
    outcome_text = None
    engine = _Engine(lambda a, t: "ok")
    instance = RuntimeInstance.from_parts(
        config=KernelConfig(agent_spec=_spec(order=["nobody"]), agent_id="moderator"),
        engine=engine,
        enable_observability=False,
        enable_coordination=False,
    )
    instance.kernel.plugin = ModeratedRoundsPlugin()
    engine.instance = instance
    outcome_text = instance.run_user_text("go").client_responses[-1].content
    assert "nobody" in outcome_text and not engine.calls


def test_the_preset_builds_a_valid_table() -> None:
    for extra in ({}, {"record_tool": "write_commitment"}):
        table = build_table({"decider": "lead", **extra}, ["lead", *SPEAKERS])
        assert validate_definition(table) == []
        assert ("record" in table["states"]) == bool(extra)
    assert table["vars"]["order"] == {"literal": SPEAKERS}
    assert table["vars"]["confirmers"] == {"literal": ["lead", *SPEAKERS]}


def test_a_decision_without_a_plan_is_unreadable() -> None:
    outcome, engine = _run(lambda agent, task: '{"action": "propose"}' if agent == "lead" else "ok")
    assert outcome["reason"] == "protocol_error"
    assert [n for n, _ in engine.calls].count("delegate_to_lead") == 2


def test_the_decider_is_told_the_schema_and_what_was_wrong_on_a_retry() -> None:
    outcome, engine = _run(lambda agent, task: '{"action": "propose"}' if agent == "lead" else "ok")
    asks = [args["task"] for name, args in engine.calls if name == "delegate_to_lead"]
    assert "JSON Schema" in asks[0] and '"required":["action"]' in asks[0]
    assert "\"plan\" is required when action is 'propose'." in asks[0]
    assert asks[1].startswith("Your last reply could not be used (plan is required when action is 'propose')")
    assert outcome["reason"] == "protocol_error"


def test_a_confirmation_must_start_with_the_word() -> None:
    def reply(agent: str, task: str) -> str:
        if agent == "lead" and '"action"' in task:
            return json.dumps({"action": "propose", "plan": PLAN})
        return "I would confirm if the date moved." if "Do you confirm" in task else "ok"

    outcome, _ = _run(reply)
    assert outcome["reason"] == "protocol_error" and "gave no confirm or reject twice" in outcome["detail"]


def test_withhold_or_escalate_without_a_reason_is_still_an_outcome() -> None:
    for action, status in (("withhold", "withheld"), ("escalate", "escalated")):
        outcome, _ = _run(_decisions({"action": action}))
        assert (outcome["status"], outcome["reason"], outcome["detail"]) == (status, "decider_decision", "")


def test_braces_in_the_texts_are_plain_text() -> None:
    outcome, engine = _run(
        _decisions({"action": "propose", "plan": PLAN}),
        decision_instructions='Reply {action} now, as {"json": 1}.',
        confirm_question="Do you confirm {this}?",
        continue_fallback="{go}",
    )
    assert outcome["status"] == "committed"
    asks = [args["task"] for _, args in engine.calls]
    assert any('Reply {action} now, as {"json": 1}.' in a for a in asks) and any(
        "Do you confirm {this}?" in a for a in asks
    )


def test_bad_params_end_the_run_with_a_message_not_an_exception() -> None:
    for params, why in (
        ({"max_rounds": "abc"}, "invalid literal"),
        ({"order": "seller_a"}, "params.order must be a list"),
        ({"rejected_request": "Version {version} and {other}"}, "can only use {version}"),
    ):
        text, engine = _raw(_decisions(), **params)
        assert text.startswith("state_machine:") and why in text and not engine.calls
    text, _ = _raw(_decisions(), decider="")
    assert "needs params.decider" in text


def test_zero_rounds_means_the_default() -> None:
    outcome, _ = _run(_decisions(*[{"action": "continue", "message": "again"}] * 8), max_rounds=0)
    assert (outcome["reason"], outcome["rounds"]) == ("round_limit", 6)


def test_confirmations_that_arrived_are_kept_when_a_later_one_is_unreadable() -> None:
    def reply(agent: str, task: str) -> str:
        if agent == "lead" and '"action"' in task:
            return json.dumps({"action": "propose", "plan": PLAN})
        if "Do you confirm" in task:
            return "confirm: fine" if agent == "seller_a" else "maybe"
        return "ok"

    outcome, _ = _run(reply, confirmers=["seller_a", "seller_b"])
    assert outcome["reason"] == "protocol_error"
    assert [(c["party"], c["decision"]) for c in outcome["confirmations"]] == [("seller_a", "confirm")]
