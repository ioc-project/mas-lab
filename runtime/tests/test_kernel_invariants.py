#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Properties of the kernel transition function, checked for every registered design pattern.

Each test drives a scripted engine through the real driver and asserts a property of the
product state or of the egress stream, not a pattern's output.
"""

from __future__ import annotations

import json
from dataclasses import MISSING, fields

import pytest
from mas.runtime.boundary.gov.ingress_chain import RegisteredIngressPlugin
from mas.runtime.boundary.gov.ingress_plugin import IngressGovDecision
from mas.runtime.boundary.gov.plugin import GovernancePluginChain
from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.engine.simulated import SimulatedEngine
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.coupling import GovDecision
from mas.runtime.kernel.state import LifecycleState, QProduct
from mas.runtime.kernel.state_serialize import q_product_from_dict, q_product_to_dict, run_from_dict, run_to_dict
from mas.runtime.machines.gov import gov_first_hold_id
from mas.runtime.registry import get_registry
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.governance import GovernanceAction, GovIngressProfile
from mas.runtime.schema.hitl import HitlResolveChoice
from mas.runtime.schema.ingress import (
    CtxAssemblyComplete,
    EngineIoReturn,
    HitlResolve,
    OperatorSteerReceived,
    ToolCallSpec,
    UserInputReceived,
)

_TABLE = {
    "start": "a",
    "states": {
        "a": {"run": "delegate", "agents": ["b"], "input": "go", "on": [{"goto": "z"}]},
        "z": {"run": "end", "result": "ok"},
    },
}


def _patterns() -> dict[str, type]:
    """One entry per registered design pattern class, keyed by a name the registry resolves."""
    registry = get_registry()
    found: dict[str, type] = {}
    for name in registry.list_names("design_pattern"):
        if registry.get("design_pattern", name) is None:
            continue
        variant = registry.resolve_by_type("design_pattern", name)
        if variant is not None:
            cls = variant.load_class()
            if cls not in found.values():
                found[name] = cls
    return found


PATTERNS = _patterns()


def _spec(cls: type) -> dict:
    spec: dict = {"communication": {"peers": ["b", "c"]}}
    if cls.__name__ == "ModeratedRoundsPlugin":
        spec["design_pattern"] = {"type": "moderated_rounds", "params": {"decider": "b"}}
    if cls.__name__ == "StateMachinePlugin":
        spec["design_pattern"] = {"type": "state_machine", "params": _TABLE}
    return spec


class Engine(SimulatedEngine):
    """The model asks for one tool, then answers; a tool returns a result. ``fail_first`` errors once."""

    def __init__(self, *, fail_first: bool = False) -> None:
        super().__init__()
        self.fail_first = fail_first
        self.llm_calls = 0
        self.asked_tool = False

    async def ainvoke(self, io):
        cid = io.correlation_id
        if io.op == "LLM_CALL":
            self.llm_calls += 1
            if self.fail_first and self.llm_calls == 1:
                return EngineIoReturn(
                    correlation_id=cid,
                    response_kind="ERROR",
                    next_step="STOP",
                    text="provider down",
                    failure_class="transient",
                )
            if not self.asked_tool:
                self.asked_tool = True
                return EngineIoReturn(
                    correlation_id=cid, response_kind="MODEL_TEXT", next_step="TOOL_CALL", tool_name="t"
                )
            return EngineIoReturn(correlation_id=cid, response_kind="MODEL_TEXT", next_step="STOP", text="answer")
        return EngineIoReturn(correlation_id=cid, response_kind="TOOL_RESULT", next_step="LLM_CALL", text="result")


class HoldTools:
    def evaluate_egress(self, intent, *, config):  # noqa: ARG002
        if intent.op == "TOOL_CALL":
            return GovDecision.HITL, "hold", "ask"
        return GovDecision.ALLOW, "hold", "ok"


class HoldResults:
    plugin_id = "hold-results"

    def evaluate_ingress(self, intent, *, config):  # noqa: ARG002
        if intent.response_kind == "TOOL_RESULT":
            return IngressGovDecision(action=GovernanceAction.HITL, message="hold")
        return IngressGovDecision(action=GovernanceAction.ALLOW, message="ok")


class SpyEgress:
    """Last in the chain: records every call that reaches it."""

    def __init__(self) -> None:
        self.seen: list[int] = []

    def evaluate_egress(self, intent, *, config):  # noqa: ARG002
        self.seen.append(intent.correlation_id)
        return GovDecision.ALLOW, "spy", "seen"


class SpyIngress:
    plugin_id = "spy-ingress"

    def __init__(self) -> None:
        self.seen = 0

    def evaluate_ingress(self, intent, *, config):  # noqa: ARG002
        self.seen += 1
        return IngressGovDecision(action=GovernanceAction.ALLOW, message="seen", chain="continue")


def _instance(cls: type, name: str, *, engine: Engine | None = None, **cfg) -> RuntimeInstance:
    config = KernelConfig(pattern_plugin_id=name, agent_id="a", agent_spec=_spec(cls), **cfg)
    inst = RuntimeInstance.from_parts(engine=engine or Engine(), config=config)
    inst.kernel.plugin = cls()
    return inst


def _default(f):
    return f.default_factory() if f.default_factory is not MISSING else f.default


def _flow_leaks(q: QProduct) -> dict:
    return {
        f.name: getattr(q, f.name)
        for f in fields(QProduct)
        if f.metadata.get("scope") == "flow" and getattr(q, f.name) != _default(f)
    }


def _outcome_kinds(inst: RuntimeInstance, trace) -> set[str]:
    kinds = set()
    if trace.client_responses:
        kinds.add("response")
    if trace.boundary_errors:
        kinds.add("error")
    if inst.kernel.q.ctrl != LifecycleState.RUNNING:
        kinds.add("not-running")  # a hold kept through the pause is not a question to ask now
    elif trace.awaiting_hitl:
        kinds.add("hitl")
    return kinds


def _steps(trace) -> list[InvokeEngineIo]:
    return [sym for step in trace.steps for sym in step.egress if isinstance(sym, InvokeEngineIo)]


# Scenarios: each yields the traces of every feed it makes.
def _plain(cls, name):
    inst = _instance(cls, name)
    yield inst, inst.run_user_text("one")
    yield inst, inst.run_user_text("two", turn_id="u2")


def _hold_and_approve(cls, name):
    inst = _instance(cls, name, egress_governance_plugin=HoldTools(), hitl_once_per_turn=True)
    trace = inst.run_user_text("one")
    yield inst, trace
    while trace.awaiting_hitl:
        trace = inst.feed(HitlResolve(request_id=gov_first_hold_id(inst.kernel.q), resolution=HitlResolveChoice.ALLOW))
        yield inst, trace
    yield inst, inst.run_user_text("two", turn_id="u2")


def _hold_and_block(cls, name):
    inst = _instance(cls, name, egress_governance_plugin=HoldTools())
    trace = inst.run_user_text("one")
    yield inst, trace
    while trace.awaiting_hitl:
        trace = inst.feed(HitlResolve(request_id=gov_first_hold_id(inst.kernel.q), resolution=HitlResolveChoice.BLOCK))
        yield inst, trace
    yield inst, inst.run_user_text("two", turn_id="u2")


def _ingress_hold_and_allow(cls, name):
    inst = _instance(
        cls,
        name,
        hitl_on_tool_result=True,
        ingress_governance_plugins=(RegisteredIngressPlugin(plugin=HoldResults()),),
    )
    trace = inst.run_user_text("one")
    yield inst, trace
    while trace.awaiting_hitl:
        trace = inst.feed(HitlResolve(request_id=gov_first_hold_id(inst.kernel.q), resolution=HitlResolveChoice.ALLOW))
        yield inst, trace
    yield inst, inst.run_user_text("two", turn_id="u2")


def _provider_error(profile):
    def scenario(cls, name):
        inst = _instance(cls, name, engine=Engine(fail_first=True), gov_ingress_profile=profile)
        yield inst, inst.run_user_text("one")
        yield inst, inst.run_user_text("two", turn_id="u2")

    return scenario


def _steer(cls, name):
    inst = _instance(cls, name)
    yield inst, inst.run_user_text("one")
    yield inst, inst.feed(OperatorSteerReceived(steer_id="s1", context_text="again"))


def _pause_resume(cls, name):
    inst = _instance(cls, name, egress_governance_plugin=HoldTools())
    trace = inst.run_user_text("one")
    yield inst, trace
    yield inst, inst.pause()
    yield inst, inst.resume()
    yield inst, inst.run_user_text("two", turn_id="u2")


class BatchEngine(Engine):
    """The model asks for three tools in one reply, then answers; a tool returns a result."""

    async def ainvoke(self, io):
        if io.op == "LLM_CALL" and not self.asked_tool:
            self.asked_tool = True
            self.llm_calls += 1
            return EngineIoReturn(
                correlation_id=io.correlation_id,
                response_kind="MODEL_TEXT",
                next_step="PARALLEL_TOOL_CALLS",
                parallel_tools=tuple(ToolCallSpec(tool_name=n) for n in ("a", "b", "c")),
                offered_tools=["a", "b", "c"],
            )
        return await super().ainvoke(io)


class RetryOnceForB:
    def __init__(self) -> None:
        self.n = 0

    def evaluate_egress(self, intent, *, config):  # noqa: ARG002
        if intent.op == "TOOL_CALL" and intent.tool_name == "b":
            self.n += 1
            if self.n == 1:
                return GovDecision.RETRY, "retry", "again"
        return GovDecision.ALLOW, "retry", "ok"


def _batch_retry(cls, name):
    inst = _instance(
        cls,
        name,
        engine=BatchEngine(),
        egress_governance_plugin=RetryOnceForB(),
        parallel_tool_calls=True,
        max_gov_retries=2,
    )
    yield inst, inst.run_user_text("one")
    yield inst, inst.run_user_text("two", turn_id="u2")


SCENARIOS = {
    "plain": _plain,
    "hold-approve": _hold_and_approve,
    "hold-block": _hold_and_block,
    "ingress-hold": _ingress_hold_and_allow,
    "error-block": _provider_error(GovIngressProfile.BLOCK_ON_ERROR),
    "error-retry": _provider_error(GovIngressProfile.RETRY_ON_ERROR),
    "steer": _steer,
    "pause-resume": _pause_resume,
    "batch-retry": _batch_retry,
}

_PARAMS = [pytest.param(name, id=name) for name in PATTERNS]


@pytest.mark.parametrize("scenario", list(SCENARIOS))
@pytest.mark.parametrize("name", _PARAMS)
def test_quiescence_every_flow_field_is_default_when_idle(name: str, scenario: str) -> None:
    # Kills: any turn-ending path (block, retry, pause, terminate) that leaves a wait, hold or
    # pending call behind.
    for inst, _trace in SCENARIOS[scenario](PATTERNS[name], name):
        q = inst.kernel.q
        if q.dp.value == "IDLE" and q.gov_state != "HITL_PENDING":
            assert _flow_leaks(q) == {}


def _liveness_violations(inst: RuntimeInstance, trace) -> str:
    # A pattern that raises is reported as a boundary error: one outcome, but a defect, not an answer.
    if broken := [e.message for e in trace.boundary_errors if e.code == "PLUGIN_ERROR"]:
        return f"the design pattern raised: {broken}"
    kinds = _outcome_kinds(inst, trace)
    if len(kinds) > 1:
        return f"more than one outcome: {sorted(kinds)}"
    if not kinds and inst.kernel.q.dp.value != "IDLE":
        return f"busy and silent: dp={inst.kernel.q.dp.value}"
    return ""


@pytest.mark.parametrize("scenario", list(SCENARIOS))
@pytest.mark.parametrize("name", _PARAMS)
def test_liveness_every_feed_ends_in_exactly_one_outcome(name: str, scenario: str) -> None:
    # Kills: a feed that returns with the kernel busy and nothing said to the client.
    traces = SCENARIOS[scenario](PATTERNS[name], name)
    problems = [found for inst, trace in traces if (found := _liveness_violations(inst, trace))]
    assert problems == []


@pytest.mark.parametrize("name", _PARAMS)
def test_every_input_that_starts_a_cycle_from_idle_resets_the_turn_scope(name: str) -> None:
    # Kills: a steer (or any new entry point) that starts a cycle without the turn reset.
    turn = [f for f in fields(QProduct) if f.metadata.get("scope") == "turn"]
    for event in (
        UserInputReceived(user_turn_id="u1", text="hi"),
        OperatorSteerReceived(steer_id="s1", context_text="again"),
    ):
        inst = _instance(PATTERNS[name], name)
        q = inst.kernel.q
        for f in turn:
            setattr(q, f.name, True if isinstance(_default(f), bool) else 3)
        inst.kernel.transition(event)
        leaked = {f.name: getattr(inst.kernel.q, f.name) for f in turn if getattr(inst.kernel.q, f.name) != _default(f)}
        assert leaked == {}, type(event).__name__


@pytest.mark.parametrize("name", _PARAMS)
def test_governance_is_never_bypassed_for_egress(name: str) -> None:
    # Kills: any path where a call reaches the engine without the whole chain having judged it.
    spy = SpyEgress()
    inst = _instance(
        PATTERNS[name],
        name,
        egress_governance_plugin=GovernancePluginChain([HoldTools(), spy]),
        hitl_once_per_turn=True,
    )
    trace = inst.run_user_text("one")
    calls = _steps(trace)
    while trace.awaiting_hitl:
        trace = inst.feed(HitlResolve(request_id=gov_first_hold_id(inst.kernel.q), resolution=HitlResolveChoice.ALLOW))
        calls += _steps(trace)
    trace = inst.run_user_text("two", turn_id="u2")
    calls += _steps(trace)
    assert calls
    assert {c.correlation_id for c in calls} <= set(spy.seen)


@pytest.mark.parametrize("name", _PARAMS)
def test_governance_is_never_bypassed_for_ingress(name: str) -> None:
    # Kills: committing an engine return to the ledger without the ingress chain having seen it.
    spy = SpyIngress()
    inst = _instance(
        PATTERNS[name],
        name,
        hitl_once_per_turn=True,
        hitl_on_tool_result=True,
        ingress_governance_plugins=(
            RegisteredIngressPlugin(plugin=spy, chain="continue"),
            RegisteredIngressPlugin(plugin=HoldResults()),
        ),
    )
    trace = inst.run_user_text("one")
    while trace.awaiting_hitl:
        trace = inst.feed(HitlResolve(request_id=gov_first_hold_id(inst.kernel.q), resolution=HitlResolveChoice.ALLOW))
    inst.driver.engine.asked_tool = False
    inst.run_user_text("two", turn_id="u2")
    committed = [r for r in inst.kernel.run.events if not r.placeholder]
    assert spy.seen >= len(committed) > 0


@pytest.mark.parametrize("name", _PARAMS)
def test_no_engine_call_while_paused(name: str) -> None:
    # Kills: a pause that only gates the user entry point; ctx completions, steers and returns went on.
    inst = _instance(PATTERNS[name], name)
    inst.pause()
    q = inst.kernel.q
    assert q.ctrl == LifecycleState.PAUSED
    for event in (
        UserInputReceived(user_turn_id="u1", text="hi"),
        CtxAssemblyComplete(collect_id="u1"),
        OperatorSteerReceived(steer_id="s1", context_text="x"),
    ):
        result = inst.kernel.transition(event)
        assert not [s for s in result.egress if isinstance(s, InvokeEngineIo)], type(event).__name__
    trace = inst.run_user_text("hi", turn_id="u2")
    assert _steps(trace) == []
    assert inst.driver.engine.llm_calls == 0


@pytest.mark.parametrize("name", _PARAMS)
def test_a_paused_kernel_ignores_every_non_lifecycle_event_and_does_not_change(name: str) -> None:
    # Kills: the orchestrator gate alone (the egress chokepoint refusing is a second layer): while paused
    # a user input, steer or context completion must not even move the state. A HITL answer to a hold is
    # remembered (row 5); a HITL answer with no hold is a no-op, covered here.
    inst = _instance(PATTERNS[name], name)
    inst.pause()
    q = inst.kernel.q
    before = q_product_to_dict(q)
    for event in (
        UserInputReceived(user_turn_id="u1", text="hi"),
        OperatorSteerReceived(steer_id="s1", context_text="x"),
        CtxAssemblyComplete(collect_id="u1"),
        HitlResolve(request_id=1, resolution=HitlResolveChoice.ALLOW),
    ):
        result = inst.kernel.transition(event)
        assert result.egress == [], type(event).__name__
        assert q_product_to_dict(inst.kernel.q) == before, type(event).__name__


def test_ingress_block_ends_the_turn_in_the_step_itself() -> None:
    # Kills: relying on the kernel's idle reset for an ingress BLOCK (the step must leave nothing open).
    from mas.runtime.kernel.ingress_step import apply_engine_io_return
    from mas.runtime.kernel.state import RunLedger

    q = QProduct()
    q.pending_engine_correlation_id = 1
    q.outbound_waits = []
    from mas.runtime.kernel.inflight import register_inflight

    register_inflight(q, 1, kind="MODEL", op="LLM_CALL")
    q.pending_engine_correlation_id = 1
    event = EngineIoReturn(correlation_id=1, response_kind="ERROR", next_step="STOP", text="down")
    apply_engine_io_return(
        q,
        RunLedger(),
        event,
        config=KernelConfig(gov_ingress_profile=GovIngressProfile.BLOCK_ON_ERROR),
        evaluate=lambda *a, **k: [],
    )
    assert (q.outbound_waits, q.dp.value) == ([], "IDLE")


def _ledger_state(run) -> tuple:
    return (
        tuple((r.correlation_id, r.seq, r.response_kind, r.text, r.placeholder) for r in run.events),
        run.allocated_high,
        run.max_seq(),
    )


@pytest.mark.parametrize("scenario", list(SCENARIOS))
@pytest.mark.parametrize("name", _PARAMS)
def test_a_checkpoint_restores_every_field_at_every_step(name: str, scenario: str) -> None:
    # Kills: a serializer that forgets a field of the product state or the ledger's id allocator and
    # sequence counter (a restored kernel then reuses an id, or loses a hold or an approval).
    for inst, _trace in SCENARIOS[scenario](PATTERNS[name], name):
        q = inst.kernel.q
        wire = json.loads(json.dumps(q_product_to_dict(q)))
        assert q_product_from_dict(wire) == q
        run = inst.kernel.run
        assert _ledger_state(run_from_dict(json.loads(json.dumps(run_to_dict(run))))) == _ledger_state(run)


def test_a_checkpoint_holds_every_field_of_the_state() -> None:
    # Kills: adding a field to the state without deciding whether a checkpoint carries it.
    carried = set(q_product_to_dict(QProduct()))
    assert {f.name for f in fields(QProduct)} - carried == {"running_spec"}


def test_a_restored_kernel_does_not_reuse_the_id_of_a_pending_hold() -> None:
    # Kills: restoring the id allocator from the rows only: the hold's id is beyond the last row, so the
    # next call got the same id and its wait replaced the hold's.
    inst = _instance(
        PATTERNS["react"],
        "react",
        hitl_on_tool_result=True,
        ingress_governance_plugins=(RegisteredIngressPlugin(plugin=HoldResults()),),
    )
    assert inst.run_user_text("one").awaiting_hitl
    hold_id = gov_first_hold_id(inst.kernel.q)
    inst.kernel.restore(json.loads(json.dumps(inst.kernel.snapshot())))
    assert inst.kernel.run.allocate_correlation_id() > hold_id
