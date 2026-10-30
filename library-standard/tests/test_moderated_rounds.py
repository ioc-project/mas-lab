#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""moderated_rounds: a whole turn against a scripted engine. No model."""

from __future__ import annotations

import json
from collections.abc import Callable

from mas.library.standard.plugins.design_patterns.moderated_rounds import (
    DEFAULT_REJECTED_REQUEST,
    ModeratedRoundsPlugin,
    parse_confirmation,
    parse_decision,
)
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


def _run(reply: Callable[[str, str], str], **params: object) -> tuple[dict, _Engine]:
    engine = _Engine(reply)
    instance = RuntimeInstance.from_parts(
        config=KernelConfig(agent_spec=_spec(**params)),
        engine=engine,
        enable_observability=False,
        enable_coordination=False,
    )
    instance.kernel.plugin = ModeratedRoundsPlugin()
    engine.instance = instance
    trace = instance.run_user_text("We need 100 units by Friday.")
    assert trace.client_responses, "the turn produced no response"
    return json.loads(trace.client_responses[-1].content), engine


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
        config=KernelConfig(agent_spec=_spec(order=["nobody"])),
        engine=engine,
        enable_observability=False,
        enable_coordination=False,
    )
    instance.kernel.plugin = ModeratedRoundsPlugin()
    engine.instance = instance
    outcome_text = instance.run_user_text("go").client_responses[-1].content
    assert "nobody" in outcome_text and not engine.calls


def test_parsers() -> None:
    assert parse_decision('```json\n{"action": "continue", "message": "x"}\n```')["message"] == "x"
    assert parse_decision('Sure. {"action": "propose", "plan": {"a": 1}}')["plan"] == {"a": 1}
    assert parse_decision('{"action": "propose"}') is None
    assert parse_decision("let's continue") is None
    assert parse_confirmation("Confirm. All good.") == ("confirm", "All good.")
    assert parse_confirmation("**Reject**: the lot is late") == ("reject", "the lot is late")
    assert parse_confirmation("I would confirm if...") is None
