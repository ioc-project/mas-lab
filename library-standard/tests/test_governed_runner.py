#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Governance and observability at the boundary of a runner, with the chokepoints that already exist."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from mas.library.standard.plugins.governance.budget import BudgetPlugin
from mas.library.standard.plugins.governance.no_undeclared_tool import NoUndeclaredToolPlugin
from mas.library.standard.plugins.runner.governed import GovernedRunner
from mas.runtime.boundary.gov.ingress_chain import RegisteredIngressPlugin
from mas.runtime.boundary.gov.ingress_plugin import IngressGovDecision
from mas.runtime.boundary.gov.plugin import GovernancePluginChain
from mas.runtime.boundary.obs.operator import ObservabilityOperator
from mas.runtime.boundary.runner import RunnerDenied, RunnerError, RunnerReply, RunnerTask
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.coupling import GovDecision
from mas.runtime.schema.governance import GovernanceAction


@dataclass
class FakeRunner:
    runner_id: str = "fake-harness"
    reply: RunnerReply = field(default_factory=lambda: RunnerReply(text="done"))
    error: Exception | None = None
    seen: list[RunnerTask] = field(default_factory=list)
    closed: int = 0

    def run(self, task: RunnerTask) -> RunnerReply:
        self.seen.append(task)
        if self.error is not None:
            raise self.error
        return self.reply

    def close(self) -> None:
        self.closed += 1


class Collector:
    def __init__(self) -> None:
        self.transitions: list = []

    def on_transition(self, transition) -> None:
        self.transitions.append(transition)

    def sessions(self, kind: str) -> list:
        return [t for t in self.transitions if t.boundary_kind == "session" and t.mealy_symbol == kind]


class DenyTask:
    """A Control that refuses tasks naming a forbidden word: it reads the task from the tool arguments."""

    plugin_id = "deny-task@v1"

    def __init__(self, word: str = "forbidden") -> None:
        self.word = word
        self.seen: list = []

    def evaluate_egress(self, intent, *, config):
        self.seen.append(intent)
        if intent.op == "TOOL_CALL" and self.word in (intent.tool_arguments or {}).get("task", ""):
            return GovDecision.BLOCK, self.plugin_id, f"task mentions {self.word!r}"
        return GovDecision.ALLOW, self.plugin_id, "clean"


def governed(inner, plugin=None, observer=None, **config):
    cfg = KernelConfig(egress_governance_plugin=plugin, **config)
    return GovernedRunner(inner, config=cfg, observer=observer, agent_id="front")


def test_a_task_is_judged_as_a_tool_call_named_for_the_runner_and_then_runs() -> None:
    plugin = DenyTask()
    inner = FakeRunner()
    reply = governed(inner, plugin).run(RunnerTask(text="hello", session_id="s", task_id="t", caller="alice"))
    assert reply.text == "done"
    (intent,) = plugin.seen
    assert (intent.op, intent.tool_name) == ("TOOL_CALL", "runner:fake-harness")
    assert intent.tool_arguments == {"task": "hello", "session_id": "s", "task_id": "t", "caller": "alice"}
    assert intent.offered_tools == ("runner:fake-harness",)


def test_a_blocked_task_never_reaches_the_runner() -> None:
    inner = FakeRunner()
    with pytest.raises(RunnerDenied) as raised:
        governed(inner, DenyTask()).run(RunnerTask(text="this is forbidden"))
    assert raised.value.decision == "BLOCK"
    assert raised.value.policy == "deny-task@v1"
    assert inner.seen == []


def test_existing_controls_apply_unchanged_budget_caps_runner_calls() -> None:
    inner = FakeRunner()
    runner = governed(inner, BudgetPlugin(max_tool_calls=2))
    runner.run(RunnerTask(text="1"))
    runner.run(RunnerTask(text="2"))
    with pytest.raises(RunnerDenied) as raised:
        runner.run(RunnerTask(text="3"))
    assert raised.value.decision == "TERMINATE"
    assert len(inner.seen) == 2


def test_a_chain_of_controls_stops_at_the_first_refusal() -> None:
    chain = GovernancePluginChain([NoUndeclaredToolPlugin(), DenyTask(), BudgetPlugin(max_tool_calls=9)])
    inner = FakeRunner()
    assert governed(inner, chain).run(RunnerTask(text="fine")).text == "done"
    with pytest.raises(RunnerDenied):
        governed(inner, chain).run(RunnerTask(text="forbidden"))


def test_a_hold_has_no_one_to_ask_so_it_refuses() -> None:
    inner = FakeRunner()
    with pytest.raises(RunnerDenied) as raised:
        governed(inner, None, hitl_on_tool=True).run(RunnerTask(text="x"))
    assert raised.value.decision == "HITL"
    assert inner.seen == []


def test_a_governance_plugin_that_raises_refuses_the_task() -> None:
    class Broken:
        def evaluate_egress(self, intent, *, config):
            raise RuntimeError("boom")

    inner = FakeRunner()
    with pytest.raises(RunnerDenied, match="boom"):
        governed(inner, Broken()).run(RunnerTask(text="x"))
    assert inner.seen == []


def test_ingress_sees_the_kind_of_the_response_and_can_refuse_it() -> None:
    @dataclass
    class BlockResults:
        plugin_id: str = "block-results"
        kinds: list = field(default_factory=list)

        def evaluate_ingress(self, intent, *, config):
            self.kinds.append(intent.response_kind)
            return IngressGovDecision(action=GovernanceAction.BLOCK, message="no results")

    ingress = BlockResults()
    cfg = KernelConfig(ingress_governance_plugins=(RegisteredIngressPlugin(plugin=ingress),))
    inner = FakeRunner()
    with pytest.raises(RunnerDenied, match="no results"):
        GovernedRunner(inner, config=cfg).run(RunnerTask(text="x"))
    assert ingress.kinds == ["TOOL_RESULT"]
    assert len(inner.seen) == 1


def test_a_runner_failure_is_not_judged_and_is_recorded() -> None:
    observer = ObservabilityOperator()
    collector = Collector()
    observer.subscribe(collector)
    inner = FakeRunner(error=RunnerError("harness down"))
    with pytest.raises(RunnerError, match="harness down"):
        governed(inner, None, observer).run(RunnerTask(text="x", session_id="s"))
    (call_error,) = [t for t in collector.sessions("runner_call") if t.attributes["phase"] == "error"]
    assert call_error.attributes["runner"] == "fake-harness"
    assert call_error.attributes["error"] == "harness down"


def test_boundary_events_carry_the_runner_and_the_harness_events() -> None:
    observer = ObservabilityOperator()
    collector = Collector()
    observer.subscribe(collector)
    inner = FakeRunner(reply=RunnerReply(text="the answer", events=({"kind": "tool_call", "name": "grep"},)))
    governed(inner, DenyTask(), observer).run(RunnerTask(text="q", session_id="s1", task_id="t1", parent_call_id="p"))

    (user_input,) = collector.sessions("user_input")
    assert user_input.attributes["text"] == "q"
    assert user_input.attributes["runner"] == "fake-harness"
    assert user_input.attributes["call_id"] == "front-t1-exec"
    assert user_input.attributes["parent_call_id"] == "p"
    (response,) = collector.sessions("agent_response")
    assert response.attributes["text"] == "the answer"
    assert response.attributes["runner"] == "fake-harness"
    phases = [t.attributes["phase"] for t in collector.sessions("runner_call")]
    assert phases == ["start", "end"]
    (event,) = collector.sessions("runner_event")
    assert event.attributes["event"] == {"kind": "tool_call", "name": "grep"}
    assert event.attributes["runner"] == "fake-harness"
    egress = [e for e in observer.events if e.kind.value == "governance.decision"]
    assert [(e.payload["hook"], e.payload["decision"]) for e in egress] == [("egress", "ALLOW"), ("ingress", "ALLOW")]
    assert egress[0].payload["op"] == "TOOL_CALL"
    assert egress[0].policy_name == "deny-task@v1"


def test_a_denied_task_is_recorded_as_denied_with_the_policy() -> None:
    observer = ObservabilityOperator()
    collector = Collector()
    observer.subscribe(collector)
    with pytest.raises(RunnerDenied):
        governed(FakeRunner(), DenyTask(), observer).run(RunnerTask(text="forbidden"))
    (denied,) = [t for t in collector.sessions("runner_call") if t.attributes["phase"] == "denied"]
    assert denied.attributes["decision"] == "BLOCK"
    assert denied.attributes["policy"] == "deny-task@v1"
    assert collector.sessions("agent_response") == []


def test_close_reaches_the_inner_runner() -> None:
    inner = FakeRunner()
    governed(inner).close()
    assert inner.closed == 1
