#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The kernel stays faithful to its transition model: approvals never skip a deny, a turn always ends clean."""

from __future__ import annotations

import copy
import json

import pytest
from mas.runtime.boundary.gov.ingress_chain import RegisteredIngressPlugin
from mas.runtime.boundary.gov.ingress_plugin import IngressGovDecision
from mas.runtime.boundary.gov.plugin import GovernancePluginChain, evaluate_egress_at_chokepoint
from mas.runtime.boundary.gov.policy import EgressIntentView
from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.engine.simulated import SimulatedEngine
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.coupling import GovDecision
from mas.runtime.kernel.egress_gate import authorize_scheduled_egress
from mas.runtime.kernel.state import DpState, LifecycleState, QProduct, RunLedger
from mas.runtime.machines.gov import gov_first_hold_id
from mas.runtime.schema.egress import NoOp
from mas.runtime.schema.governance import GovernanceAction, GovIngressProfile
from mas.runtime.schema.hitl import HitlResolveChoice
from mas.runtime.schema.ingress import (
    EngineIoReturn,
    HitlApprove,
    HitlResolve,
    OperatorSteerReceived,
    ToolCallSpec,
)


def _ret(io, **kw):
    kw.setdefault("response_kind", "MODEL_TEXT")
    kw.setdefault("next_step", "STOP")
    return EngineIoReturn(correlation_id=io.correlation_id, **kw)


class ScriptedEngine(SimulatedEngine):
    """LLM calls follow ``script`` (tool names, then a final answer); tools return a result."""

    def __init__(self, tools: list[str] | None = None, *, tool_text: dict[str, str] | None = None) -> None:
        super().__init__()
        self.tools = list(tools or [])
        self.tool_text = tool_text or {}
        self.llm_calls = 0
        self.tool_runs: list[str] = []
        self._last_tool = ""

    async def ainvoke(self, io):
        if io.op == "LLM_CALL":
            self.llm_calls += 1
            if self.tools:
                self._last_tool = self.tools.pop(0)
                return _ret(io, next_step="TOOL_CALL", tool_name=self._last_tool)
            return _ret(io, text=f"answer-{self.llm_calls}")
        self.tool_runs.append(self._last_tool)
        return _ret(
            io,
            response_kind="TOOL_RESULT",
            next_step="LLM_CALL",
            text=self.tool_text.get(self._last_tool, "result"),
        )


class HoldEveryTool:
    def evaluate_egress(self, intent, *, config):  # noqa: ARG002
        if intent.op == "TOOL_CALL":
            return GovDecision.HITL, "hold", "ask a human"
        return GovDecision.ALLOW, "hold", "ok"


class BlockTool:
    def __init__(self, name: str) -> None:
        self.name = name

    def evaluate_egress(self, intent, *, config):  # noqa: ARG002
        if intent.tool_name == self.name:
            return GovDecision.BLOCK, "no", "forbidden"
        return GovDecision.ALLOW, "no", "ok"


def _approve(inst) -> object:
    return inst.feed(HitlResolve(request_id=gov_first_hold_id(inst.kernel.q), resolution=HitlResolveChoice.ALLOW))


def test_approval_never_skips_a_later_plugin_deny_once_per_turn() -> None:
    # Kills: an approval (or hitl_once_per_turn) that short-circuits the chain, so delete_all runs.
    engine = ScriptedEngine(["read_file", "delete_all"])
    plugin = GovernancePluginChain([HoldEveryTool(), BlockTool("delete_all")])
    inst = RuntimeInstance.from_parts(
        engine=engine, config=KernelConfig(egress_governance_plugin=plugin, hitl_once_per_turn=True)
    )
    inst.run_user_text("go")
    _approve(inst)
    assert engine.tool_runs == ["read_file"]


class HoldThenBlockResult:
    plugin_id = "result-gate"

    def evaluate_ingress(self, intent, *, config):  # noqa: ARG002
        if intent.response_kind != "TOOL_RESULT":
            return IngressGovDecision(action=GovernanceAction.ALLOW, message="ok")
        if "secret" in intent.error_text:
            return IngressGovDecision(action=GovernanceAction.BLOCK, message="secret result")
        return IngressGovDecision(action=GovernanceAction.HITL, message="hold")


def test_ingress_once_per_turn_still_lets_the_same_plugin_block() -> None:
    # Kills: the ingress once-per-turn skip dropping the validate step, so the same plugin cannot BLOCK later.
    engine = ScriptedEngine(["a", "b"], tool_text={"b": "secret value"})
    cfg = KernelConfig(
        hitl_once_per_turn=True,
        hitl_on_tool_result=True,
        ingress_governance_plugins=(RegisteredIngressPlugin(plugin=HoldThenBlockResult()),),
    )
    inst = RuntimeInstance.from_parts(engine=engine, config=cfg)
    inst.run_user_text("go")
    t = _approve(inst)
    ledger = [(r.correlation_id, r.text) for r in inst.kernel.run.events if r.response_kind == "TOOL_RESULT"]
    assert all("secret" not in text for _, text in ledger), ledger
    assert [e.code for e in t.boundary_errors]


class HoldEveryResultOnce:
    plugin_id = "hold-every-result"

    def evaluate_ingress(self, intent, *, config):  # noqa: ARG002
        if intent.response_kind == "TOOL_RESULT":
            return IngressGovDecision(action=GovernanceAction.HITL, message="hold")
        return IngressGovDecision(action=GovernanceAction.ALLOW, message="ok")


class BlockSecretResult:
    plugin_id = "block-secret-result"

    def evaluate_ingress(self, intent, *, config):  # noqa: ARG002
        if intent.response_kind == "TOOL_RESULT" and "secret" in intent.error_text:
            return IngressGovDecision(action=GovernanceAction.BLOCK, message="secret result")
        return IngressGovDecision(action=GovernanceAction.ALLOW, message="ok")


def test_ingress_approval_never_skips_a_later_plugin_deny_once_per_turn() -> None:
    # Kills: converting an earlier plugin's HITL to ALLOW without asking the rest of the chain, so a later
    # BLOCK never runs (secret lands in the ledger).
    engine = ScriptedEngine(["a", "b"], tool_text={"b": "secret value"})
    cfg = KernelConfig(
        hitl_once_per_turn=True,
        ingress_governance_plugins=(
            RegisteredIngressPlugin(plugin=HoldEveryResultOnce()),
            RegisteredIngressPlugin(plugin=BlockSecretResult()),
        ),
    )
    inst = RuntimeInstance.from_parts(engine=engine, config=cfg)
    inst.run_user_text("go")
    t = _approve(inst)
    ledger = [(r.correlation_id, r.text) for r in inst.kernel.run.events if r.response_kind == "TOOL_RESULT"]
    assert all("secret" not in text for _, text in ledger), ledger
    assert [e.code for e in t.boundary_errors]


class ErrorOnce(SimulatedEngine):
    """The first LLM call fails at the provider; later ones answer."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def ainvoke(self, io):
        self.calls += 1
        if self.calls == 1:
            return _ret(io, response_kind="ERROR", text="provider down", failure_class="transient")
        return _ret(io, text=f"answer-{self.calls}")


def _no_waits(inst) -> None:
    q = inst.kernel.q
    assert q.outbound_waits == []
    assert q.inflight_correlation_ids == []
    assert q.pending_tools_by_cid == {}
    assert q.pending_engine_correlation_id == 0


def test_a_blocked_ingress_error_ends_the_turn_clean() -> None:
    # Kills: ingress BLOCK leaving the failed call's wait open, so the next turn is never answered.
    inst = RuntimeInstance.from_parts(
        engine=ErrorOnce(), config=KernelConfig(gov_ingress_profile=GovIngressProfile.BLOCK_ON_ERROR)
    )
    first = inst.run_user_text("one")
    assert [e.code for e in first.boundary_errors]
    _no_waits(inst)
    second = inst.run_user_text("two", turn_id="u2")
    assert [r.content for r in second.client_responses]


def test_a_retried_ingress_error_is_answered_and_the_next_turn_runs() -> None:
    # Kills: ingress RETRY leaving the first call's wait open, or retrying the wrong call.
    inst = RuntimeInstance.from_parts(
        engine=ErrorOnce(), config=KernelConfig(gov_ingress_profile=GovIngressProfile.RETRY_ON_ERROR)
    )
    first = inst.run_user_text("one")
    assert [r.content for r in first.client_responses] == ["answer-2"]
    _no_waits(inst)
    second = inst.run_user_text("two", turn_id="u2")
    assert [r.content for r in second.client_responses] == ["answer-3"]


class HoldResultsOnce:
    plugin_id = "hold-results"

    def __init__(self) -> None:
        self.armed = True

    def evaluate_ingress(self, intent, *, config):  # noqa: ARG002
        if intent.response_kind == "TOOL_RESULT" and self.armed:
            self.armed = False
            return IngressGovDecision(action=GovernanceAction.HITL, message="hold")
        return IngressGovDecision(action=GovernanceAction.ALLOW, message="ok")


class HoldTool:
    def __init__(self, name: str) -> None:
        self.name = name

    def evaluate_egress(self, intent, *, config):  # noqa: ARG002
        if intent.op == "TOOL_CALL" and intent.tool_name == self.name:
            return GovDecision.HITL, "hold", "hold"
        return GovDecision.ALLOW, "hold", "ok"


def test_pause_keeps_a_pending_ingress_hold_and_the_answer_still_resolves_it() -> None:
    # Kills: pause dropping a hold (data loss) or leaving governance and the hold out of step.
    engine = ScriptedEngine(["first"])
    cfg = KernelConfig(
        egress_governance_plugin=HoldTool("second"),
        ingress_governance_plugins=(RegisteredIngressPlugin(plugin=HoldResultsOnce()),),
    )
    inst = RuntimeInstance.from_parts(engine=engine, config=cfg)
    first = inst.run_user_text("turn1")
    assert first.awaiting_hitl
    inst.pause()
    inst.resume()
    q = inst.kernel.q
    assert [h.is_ingress for h in q.hitl_holds.values()] == [True]
    assert q.gov_state == "HITL_PENDING"
    answered = _approve(inst)
    assert [r.content for r in answered.client_responses] == ["answer-2"]
    assert q.hitl_holds == {}
    engine.tools = ["second"]
    second = inst.run_user_text("turn2", turn_id="u2")
    assert second.awaiting_hitl
    _approve(inst)
    assert engine.tool_runs[-1] == "second"


def test_a_steer_from_idle_starts_a_clean_turn() -> None:
    # Kills: a steer cycle that keeps the previous turn's approvals and retry count.
    inst = RuntimeInstance.from_parts(engine=ScriptedEngine(), config=KernelConfig())
    inst.run_user_text("one")
    q = inst.kernel.q
    q.hitl_tools_approved_turn = True
    q.gov_retry_count = 2
    inst.feed(OperatorSteerReceived(steer_id="s1", context_text="again"))
    assert (q.hitl_tools_approved_turn, q.gov_retry_count) == (False, 0)


def test_a_steer_while_a_hold_is_pending_leaves_the_hold_alone() -> None:
    # Kills: a steer overwriting governance state, so the pending approval can never be answered.
    engine = ScriptedEngine(["t"])
    inst = RuntimeInstance.from_parts(engine=engine, config=KernelConfig(egress_governance_plugin=HoldEveryTool()))
    assert inst.run_user_text("go").awaiting_hitl
    q = inst.kernel.q
    before = (q.dp, q.gov_state, dict(q.hitl_holds), list(q.outbound_waits))
    refused = inst.feed(OperatorSteerReceived(steer_id="s1", context_text="change course"))
    assert [(e.code, e.recoverable) for e in refused.boundary_errors] == [("STEER_REFUSED", True)]
    assert (q.dp, q.gov_state, dict(q.hitl_holds), list(q.outbound_waits)) == before
    _approve(inst)
    assert engine.tool_runs == ["t"]


def test_egress_gate_refuses_unless_running() -> None:
    # Kills: the chokepoint authorizing a scheduled call while the lifecycle is paused or stopped.
    for state in (LifecycleState.PAUSED, LifecycleState.STOPPED):
        q = QProduct()
        q.ctrl = state
        q.scheduled_egress = "LLM_CALL"
        out = authorize_scheduled_egress(q, RunLedger(), KernelConfig())
        assert out == [NoOp()]
        assert (q.scheduled_egress, q.dp) == ("NONE", DpState.IDLE)


class BatchEngine(SimulatedEngine):
    """The model asks for several tools in one reply, then answers."""

    def __init__(self, names: list[str], fail: dict[str, int] | None = None) -> None:
        super().__init__()
        self.names = names
        self.fail = dict(fail or {})
        self.asked = False
        self.tool_runs: list[str] = []
        self.by_cid: dict[int, str] = {}
        self.scheduled = "?"

    def set_tool_for_correlation(self, cid, name, args):  # noqa: ARG002
        self.by_cid[cid] = name

    def set_scheduled_tool(self, name, args):  # noqa: ARG002
        self.scheduled = name

    async def ainvoke(self, io):
        if io.op == "LLM_CALL":
            if not self.asked:
                self.asked = True
                return _ret(
                    io,
                    next_step="PARALLEL_TOOL_CALLS",
                    parallel_tools=tuple(ToolCallSpec(tool_name=n) for n in self.names),
                    offered_tools=list(self.names),
                )
            return _ret(io, text="final")
        name = self.by_cid.get(io.correlation_id) or self.scheduled
        self.tool_runs.append(name)
        if self.fail.get(name, 0) > 0:
            self.fail[name] -= 1
            return _ret(io, response_kind="ERROR", text="boom", failure_class="transient")
        return _ret(io, response_kind="TOOL_RESULT", next_step="LLM_CALL", text=f"{name}-ok")


def test_a_parallel_batch_with_an_allowed_call_before_a_held_one_keeps_its_hold() -> None:
    # Kills: closing the abandoned first call's envelope resetting gov_state from HITL_PENDING to IDLE,
    # which wedged the batch (the hold was pending but could not be answered).
    engine = BatchEngine(["read_file", "delete_file"])
    inst = RuntimeInstance.from_parts(
        engine=engine, config=KernelConfig(egress_governance_plugin=HoldTool("delete_file"), parallel_tool_calls=True)
    )
    first = inst.run_user_text("go")
    assert first.awaiting_hitl
    assert inst.kernel.q.gov_state == "HITL_PENDING"
    assert engine.tool_runs == ["read_file"]  # the call judged before the hold runs without waiting for the answer
    t = _approve(inst)
    assert sorted(engine.tool_runs) == ["delete_file", "read_file"]
    assert [r.content for r in t.client_responses] == ["final"]


class _Fixed:
    def __init__(self, decision: GovDecision) -> None:
        self.decision = decision

    def evaluate_egress(self, intent, *, config):  # noqa: ARG002
        return self.decision, f"p-{self.decision.value}", "r"


def test_an_approval_answers_only_the_hold_whatever_the_chain_order() -> None:
    # Kills: applying the approval to the chain's aggregated verdict, which lost a later plugin's
    # RETRY, MODIFY or LOG when the HITL plugin came first.
    intent = EgressIntentView(op="TOOL_CALL", destructive=True, correlation_id=7, tool_name="x")
    for later in (GovDecision.RETRY, GovDecision.MODIFY, GovDecision.LOG):
        for plugins in ([_Fixed(GovDecision.HITL), _Fixed(later)], [_Fixed(later), _Fixed(GovDecision.HITL)]):
            cfg = KernelConfig(egress_governance_plugin=GovernancePluginChain(plugins))
            assert evaluate_egress_at_chokepoint(intent, config=cfg, hitl_gov_override=True)[0] == later


def test_a_retried_tool_of_a_parallel_batch_stays_a_tool_call() -> None:
    # Kills: a retried call's new correlation id classified LLM_CALL once a sibling's result moved the
    # tool machine off EXECUTING (a second retry then re-asked the model, and its frame leaked).
    engine = BatchEngine(["alpha", "beta"], fail={"alpha": 2})
    inst = RuntimeInstance.from_parts(
        engine=engine,
        config=KernelConfig(
            parallel_tool_calls=True, gov_ingress_profile=GovIngressProfile.RETRY_ON_ERROR, max_gov_retries=2
        ),
    )
    t = inst.run_user_text("go")
    assert engine.tool_runs.count("alpha") == 3
    assert [r.content for r in t.client_responses] == ["final"]
    _no_waits(inst)
    assert [r.content for r in inst.run_user_text("again", turn_id="u2").client_responses]


class _RaiseOnce:
    def __init__(self) -> None:
        self.n = 0

    def evaluate_egress(self, intent, *, config):  # noqa: ARG002
        self.n += 1
        if self.n == 1:
            raise RuntimeError("plugin down")
        return GovDecision.ALLOW, "ok", "ok"


def test_a_governance_plugin_error_ends_its_turn_and_the_next_turn_runs() -> None:
    # Pins the contract: the error is visible after its turn (gov_state ERROR) and is not a latch.
    inst = RuntimeInstance.from_parts(
        engine=ScriptedEngine(), config=KernelConfig(egress_governance_plugin=_RaiseOnce())
    )
    first = inst.run_user_text("one")
    assert [e.code for e in first.boundary_errors] == ["GOV_PLUGIN_ERROR"]
    assert inst.kernel.q.gov_state == "ERROR"
    second = inst.run_user_text("two", turn_id="u2")
    assert [r.content for r in second.client_responses] == ["answer-1"]
    assert inst.kernel.q.gov_state == "IDLE"


# --- Holds are a keyed collection; a pause keeps them -------------------------------------------------


class HoldEveryResult:
    plugin_id = "hold-every-result"

    def evaluate_ingress(self, intent, *, config):  # noqa: ARG002
        if intent.response_kind == "TOOL_RESULT":
            return IngressGovDecision(action=GovernanceAction.HITL, message="hold")
        return IngressGovDecision(action=GovernanceAction.ALLOW, message="ok")


def _answer(inst, request_id: int, choice: HitlResolveChoice = HitlResolveChoice.ALLOW):
    return inst.feed(HitlResolve(request_id=request_id, resolution=choice))


def _batch_with_result_holds(names: list[str]):
    engine = BatchEngine(names)
    inst = RuntimeInstance.from_parts(
        engine=engine,
        config=KernelConfig(
            parallel_tool_calls=True,
            hitl_on_tool_result=True,
            ingress_governance_plugins=(RegisteredIngressPlugin(plugin=HoldEveryResult()),),
        ),
    )
    return engine, inst


def test_concurrent_result_holds_coexist_and_each_resolves_on_its_own() -> None:
    # Kills: a one-slot hold that the second result overwrote, so the first call's wait was never answered.
    engine, inst = _batch_with_result_holds(["alpha", "beta"])
    first = inst.run_user_text("go")
    q = inst.kernel.q
    assert first.awaiting_hitl
    ids = sorted(q.hitl_holds)
    assert len(ids) == 2
    assert all(h.is_ingress for h in q.hitl_holds.values())
    assert {h.tool_name for h in q.hitl_holds.values()} == {"alpha", "beta"}
    # Answer them out of order: the later one first. The turn goes on only after both.
    middle = _answer(inst, ids[1])
    assert middle.client_responses == []
    assert sorted(q.hitl_holds) == [ids[0]]
    last = _answer(inst, ids[0])
    assert [r.content for r in last.client_responses] == ["final"]
    assert q.hitl_holds == {}
    _no_waits(inst)


def test_a_denied_result_hold_ends_the_turn_with_its_sibling_holds() -> None:
    # Kills: a denial that leaves the other holds behind, so the next turn starts with a stale question.
    _engine, inst = _batch_with_result_holds(["alpha", "beta"])
    inst.run_user_text("go")
    q = inst.kernel.q
    denied = _answer(inst, min(q.hitl_holds), HitlResolveChoice.BLOCK)
    assert denied.client_responses
    assert q.hitl_holds == {}
    assert q.gov_state == "IDLE"
    _no_waits(inst)


def _held_tool_instance():
    engine = ScriptedEngine(["t"])
    inst = RuntimeInstance.from_parts(engine=engine, config=KernelConfig(egress_governance_plugin=HoldEveryTool()))
    assert inst.run_user_text("go").awaiting_hitl
    return engine, inst


def test_pause_with_a_pending_hold_then_resume_runs_the_held_call_once_when_approved() -> None:
    # Kills: pause dropping the hold (the call is lost) or resume running a call nobody approved.
    engine, inst = _held_tool_instance()
    q = inst.kernel.q
    (hold_id,) = q.hitl_holds
    inst.pause()
    assert list(q.hitl_holds) == [hold_id]
    assert q.gov_state == "HITL_PENDING"
    inst.resume()
    assert engine.tool_runs == []  # resume alone approves nothing
    done = _answer(inst, hold_id)
    assert engine.tool_runs == ["t"]
    assert [r.content for r in done.client_responses] == ["answer-2"]
    assert q.hitl_holds == {}


def test_an_approval_that_arrives_while_paused_runs_the_held_call_only_on_resume() -> None:
    # Kills: scheduling egress while paused, or losing the answer that came in during the pause.
    engine, inst = _held_tool_instance()
    q = inst.kernel.q
    (hold_id,) = q.hitl_holds
    inst.pause()
    during = _answer(inst, hold_id)
    assert engine.tool_runs == [] and during.client_responses == []
    assert q.hitl_holds[hold_id].deferred
    resumed = inst.resume()
    assert engine.tool_runs == ["t"]
    assert [r.content for r in resumed.client_responses] == ["answer-2"]
    assert q.hitl_holds == {}
    inst.resume()
    assert engine.tool_runs == ["t"]  # released exactly once


def test_a_hitl_approve_while_paused_is_deferred_the_same_way() -> None:
    # Kills: HitlApprove being dropped while paused.
    engine, inst = _held_tool_instance()
    inst.pause()
    inst.feed(HitlApprove())
    assert engine.tool_runs == []
    inst.resume()
    assert engine.tool_runs == ["t"]


def test_a_denial_that_arrives_while_paused_is_honoured_on_resume() -> None:
    # Kills: a denied approval turning into a run, or being forgotten, across a pause.
    engine, inst = _held_tool_instance()
    q = inst.kernel.q
    (hold_id,) = q.hitl_holds
    inst.pause()
    _answer(inst, hold_id, HitlResolveChoice.BLOCK)
    resumed = inst.resume()
    assert engine.tool_runs == []
    assert resumed.client_responses and "blocked" in resumed.client_responses[0].content
    assert q.hitl_holds == {}
    _no_waits(inst)


def test_a_checkpoint_round_trip_keeps_a_hold_and_an_answer_deferred_by_a_pause() -> None:
    # Kills: a serializer that drops the hold collection or its deferred answer.
    engine, inst = _held_tool_instance()
    q = inst.kernel.q
    (hold_id,) = q.hitl_holds
    inst.pause()
    _answer(inst, hold_id)
    data = inst.kernel.snapshot()
    inst.kernel.restore(data)
    restored = inst.kernel.q
    assert restored is not q
    assert restored.hitl_holds == q.hitl_holds
    assert restored.hitl_holds[hold_id].tool_name == "t"
    inst.resume()
    assert engine.tool_runs == ["t"]


def test_abort_drops_the_holds() -> None:
    # Kills: an abort that keeps a question nobody can answer any more.
    _engine, inst = _held_tool_instance()
    inst.abort()
    q = inst.kernel.q
    assert q.hitl_holds == {} and q.gov_state == "IDLE"


class _BlacklistOnce:
    """Blacklists tool ``a`` the first time it is asked about it; allows everything else."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def evaluate_egress(self, intent, *, config):  # noqa: ARG002
        if intent.op != "TOOL_CALL":
            return GovDecision.ALLOW, "bl", "ok"
        self.asked.append(intent.tool_name)
        if intent.tool_name == "a" and self.asked.count("a") == 1:
            return GovDecision.BLACKLIST, "bl", "no more a"
        return GovDecision.ALLOW, "bl", "ok"


def test_a_blacklist_covers_the_named_tool_for_the_session_and_no_other_tool() -> None:
    # Kills: a session-wide boolean, which skipped every later tool call once any tool was blacklisted.
    engine = ScriptedEngine(["a"])
    plugin = _BlacklistOnce()
    inst = RuntimeInstance.from_parts(engine=engine, config=KernelConfig(egress_governance_plugin=plugin))
    inst.run_user_text("one")
    assert inst.kernel.q.tool_blacklist == ["a"]
    engine.tools = ["b"]
    inst.run_user_text("two", turn_id="u2")
    assert engine.tool_runs == ["b"]
    engine.tools = ["a"]
    inst.run_user_text("three", turn_id="u3")
    assert engine.tool_runs == ["b"]  # a stays skipped, and the chain is not asked about it again
    assert plugin.asked == ["a", "b"]


def test_a_hold_in_a_parallel_batch_keeps_the_calls_before_it_and_queues_the_ones_after_it() -> None:
    # Kills: a batch that abandons the calls it already judged when one call is held (they never ran) or
    # drops the calls after the held one.
    engine = BatchEngine(["read_file", "delete_file", "write_file"])
    inst = RuntimeInstance.from_parts(
        engine=engine, config=KernelConfig(egress_governance_plugin=HoldTool("delete_file"), parallel_tool_calls=True)
    )
    assert inst.run_user_text("go").awaiting_hitl
    assert engine.tool_runs == ["read_file"]
    done = _approve(inst)
    assert engine.tool_runs == ["read_file", "delete_file", "write_file"]
    assert [r.content for r in done.client_responses] == ["final"]
    _no_waits(inst)
    assert inst.kernel.q.queued_tools == []


def test_skipping_a_held_call_of_a_batch_still_runs_the_rest_before_the_model_is_asked() -> None:
    # Kills: a SKIP that asks the model while sibling calls are still running or queued.
    engine = BatchEngine(["read_file", "delete_file", "write_file"])
    inst = RuntimeInstance.from_parts(
        engine=engine, config=KernelConfig(egress_governance_plugin=HoldTool("delete_file"), parallel_tool_calls=True)
    )
    inst.run_user_text("go")
    done = _answer(inst, gov_first_hold_id(inst.kernel.q), HitlResolveChoice.SKIP)
    assert engine.tool_runs == ["read_file", "write_file"]
    assert [r.content for r in done.client_responses] == ["final"]
    _no_waits(inst)


class _RetryTimes:
    def __init__(self, times: int) -> None:
        self.times = times
        self.asked = 0

    def evaluate_egress(self, intent, *, config):  # noqa: ARG002
        if intent.op != "TOOL_CALL":
            return GovDecision.ALLOW, "r", "ok"
        self.asked += 1
        if self.asked <= self.times:
            return GovDecision.RETRY, "r", "try again"
        return GovDecision.ALLOW, "r", "ok"


def test_an_egress_retry_asks_the_chain_again_and_the_call_then_runs() -> None:
    # Kills: an egress RETRY that is recorded but never driven, leaving the turn silent and busy.
    engine = ScriptedEngine(["t"])
    plugin = _RetryTimes(2)
    inst = RuntimeInstance.from_parts(
        engine=engine, config=KernelConfig(egress_governance_plugin=plugin, max_gov_retries=2)
    )
    done = inst.run_user_text("go")
    assert plugin.asked == 3
    assert engine.tool_runs == ["t"]
    assert [r.content for r in done.client_responses] == ["answer-2"]
    _no_waits(inst)


def test_an_egress_retry_is_bounded_and_then_ends_the_turn_with_an_error() -> None:
    # Kills: an unbounded retry loop, or a retry that exhausts silently.
    engine = ScriptedEngine(["t"])
    plugin = _RetryTimes(99)
    inst = RuntimeInstance.from_parts(
        engine=engine, config=KernelConfig(egress_governance_plugin=plugin, max_gov_retries=2)
    )
    done = inst.run_user_text("go")
    assert [e.code for e in done.boundary_errors] == ["GOV_RETRY_EXHAUSTED"]
    assert plugin.asked == 3 and engine.tool_runs == []
    _no_waits(inst)


class _SpyIntents:
    def __init__(self) -> None:
        self.seen: list[tuple[str, str, dict]] = []

    def evaluate_egress(self, intent, *, config):  # noqa: ARG002
        self.seen.append((intent.op, intent.tool_name, dict(intent.tool_arguments or {})))
        return GovDecision.ALLOW, "spy", "ok"


def test_an_llm_intent_carries_no_tool_fields_from_the_previous_call() -> None:
    # Kills: the model call after a tool result being judged under that tool's name and arguments.
    engine = ScriptedEngine(["t"])
    spy = _SpyIntents()
    inst = RuntimeInstance.from_parts(engine=engine, config=KernelConfig(egress_governance_plugin=spy))
    inst.run_user_text("go")
    assert [op for op, _n, _a in spy.seen] == ["LLM_CALL", "TOOL_CALL", "LLM_CALL"]
    for op, name, args in spy.seen:
        if op == "LLM_CALL":
            assert (name, args) == ("", {})
    assert spy.seen[1][1] == "t"


class _ProbeInflight(ScriptedEngine):
    """Records whether the session's interruptible-task entry exists while each call runs."""

    def __init__(self, tools):
        super().__init__(tools)
        self.inst = None
        self.entry: dict[str, list[bool]] = {"LLM_CALL": [], "TOOL_CALL": []}

    async def ainvoke(self, io):
        from mas.runtime.engine.inflight_llm import has_inflight

        self.entry[io.op].append(has_inflight(self.inst.driver.session_id))
        return await super().ainvoke(io)


async def test_only_a_model_call_registers_as_the_interruptible_task_of_a_session() -> None:
    # Kills: tools registering under the one-per-session key, so a steer cancelled a tool and a finished
    # tool cleared the model call's entry.
    engine = _ProbeInflight(["t"])
    inst = RuntimeInstance.from_parts(engine=engine, config=KernelConfig())
    engine.inst = inst
    await inst.arun_user_text("go")
    assert engine.entry == {"LLM_CALL": [True, True], "TOOL_CALL": [False]}


def _failing_pattern(inst):
    from mas.runtime.kernel.state import RunEvent

    def failing(q, run, event, *, config):  # noqa: ARG001
        q.hitl_tools_approved_turn = True
        q.dp = DpState.EVALUATING
        run.append(RunEvent(correlation_id=run.allocate_correlation_id(), response_kind="MODEL_TEXT", next_step="STOP"))
        raise RuntimeError("pattern bug")

    inst.kernel.plugin.handle_event = failing


def test_a_pattern_that_fails_midway_leaves_neither_state_nor_ledger_half_applied() -> None:
    # Kills: a pattern error reported as PLUGIN_ERROR while its writes (a ledger row, a changed machine state,
    # a spent correlation id) stayed, so the next event met an inconsistent kernel.
    from mas.runtime.kernel.state_serialize import run_to_dict

    inst = RuntimeInstance.from_parts(engine=ScriptedEngine(), config=KernelConfig())
    inst.run_user_text("one")
    real = inst.kernel.plugin.handle_event
    ledger_before = run_to_dict(inst.kernel.run)
    _failing_pattern(inst)
    failed = inst.run_user_text("two", turn_id="u2")
    assert [e.code for e in failed.boundary_errors] == ["PLUGIN_ERROR"]
    q = inst.kernel.q
    assert (q.dp, q.hitl_tools_approved_turn) == (DpState.IDLE, False)
    assert run_to_dict(inst.kernel.run) == ledger_before
    inst.kernel.plugin.handle_event = real
    assert [r.content for r in inst.run_user_text("three", turn_id="u3").client_responses] == ["answer-2"]


def test_a_transition_that_raises_restores_the_state_and_the_ledger(monkeypatch) -> None:
    # Kills: an unexpected exception inside a step leaving the state and the ledger half applied.
    from mas.runtime.kernel import orchestrator
    from mas.runtime.kernel.state import RunEvent
    from mas.runtime.kernel.state_serialize import q_product_to_dict, run_to_dict
    from mas.runtime.schema.ingress import UserInputReceived

    inst = RuntimeInstance.from_parts(engine=ScriptedEngine(), config=KernelConfig())
    inst.run_user_text("one")
    kernel = inst.kernel

    def broken(plugin, q, run, event, *, config, rollback):  # noqa: ARG001
        q.hitl_tools_approved_turn = True
        q.dp = DpState.EVALUATING
        run.append(RunEvent(correlation_id=run.allocate_correlation_id(), response_kind="MODEL_TEXT", next_step="STOP"))
        raise MemoryError("not a pattern error")

    monkeypatch.setattr(orchestrator, "step_design_pattern", broken)
    before = (q_product_to_dict(kernel.q), run_to_dict(kernel.run))
    with pytest.raises(MemoryError):
        kernel.transition(UserInputReceived(user_turn_id="u2", text="two"))
    assert (q_product_to_dict(kernel.q), run_to_dict(kernel.run)) == before


def test_an_answer_deferred_by_a_pause_is_released_once_even_if_the_hold_outlives_it() -> None:
    # Kills: a deferred answer that stays on its hold after resume and is replayed at every later resume
    # (an answer a result hold cannot take, TERMINATE, leaves the hold in place).
    _engine, inst = _batch_with_result_holds(["alpha"])
    inst.run_user_text("go")
    q = inst.kernel.q
    (hold_id,) = q.hitl_holds
    inst.pause()
    _answer(inst, hold_id, HitlResolveChoice.TERMINATE)
    assert q.hitl_holds[hold_id].deferred
    inst.resume()
    assert list(q.hitl_holds) == [hold_id] and q.hitl_holds[hold_id].deferred == {}


# --- Phase 3: answers during a pause, call ids, retries in a batch, held state across a pause ---------------


def _held_tool():
    engine = ScriptedEngine(["t"])
    inst = RuntimeInstance.from_parts(engine=engine, config=KernelConfig(egress_governance_plugin=HoldEveryTool()))
    inst.run_user_text("go")
    return engine, inst


@pytest.mark.parametrize("order", [("BLOCK", "ALLOW"), ("ALLOW", "BLOCK")])
def test_a_denial_given_while_paused_wins_over_an_approval_in_either_order(order) -> None:
    # Kills: a later answer overwriting the stored one, so a refusal given during a pause was undone.
    engine, inst = _held_tool()
    (rid,) = inst.kernel.q.hitl_holds
    inst.pause()
    for name in order:
        _answer(inst, rid, HitlResolveChoice[name])
    done = inst.resume()
    assert engine.tool_runs == []
    assert done.client_responses and "blocked" in done.client_responses[0].content


def test_a_second_denial_while_paused_does_not_replace_the_first(caplog) -> None:
    # Kills: the stored answer being replaced by a later answer of the same strength (first answer wins).
    engine, inst = _held_tool()
    (rid,) = inst.kernel.q.hitl_holds
    inst.pause()
    first = HitlResolve(request_id=rid, resolution=HitlResolveChoice.BLOCK, operator_context={"steering": "first"})
    inst.feed(first)
    inst.feed(HitlResolve(request_id=rid, resolution=HitlResolveChoice.SKIP))
    assert inst.kernel.q.hitl_holds[rid].deferred["resolution"] == "BLOCK"
    assert "already has an answer" in caplog.text


def _budget_instance(*plugins, tools=("t",), **cfg):
    from mas.library.standard.plugins.governance.budget import BudgetPlugin

    budget = BudgetPlugin(max_tool_calls=1)
    engine = ScriptedEngine(list(tools))
    chain = GovernancePluginChain([*plugins, budget])
    inst = RuntimeInstance.from_parts(engine=engine, config=KernelConfig(egress_governance_plugin=chain, **cfg))
    return engine, inst, budget


def test_an_approved_call_is_judged_under_the_same_id_so_a_counting_plugin_counts_it_once() -> None:
    # Kills: the approved pass running under a new correlation id, so budget counted the call twice and stopped it.
    engine, inst, budget = _budget_instance(HoldEveryTool())
    assert inst.run_user_text("go").awaiting_hitl
    done = _approve(inst)
    assert done.boundary_errors == []
    assert engine.tool_runs == ["t"] and budget.tool_calls == 1
    assert [r.content for r in done.client_responses] == ["answer-2"]


class _RetryWhenApproved:
    """Asks for a retry the first time it sees a call a human approved."""

    def __init__(self) -> None:
        self.retried = False

    def evaluate_egress(self, intent, *, config):  # noqa: ARG002
        if intent.op == "TOOL_CALL" and intent.hitl_approved and not self.retried:
            self.retried = True
            return GovDecision.RETRY, "r", "again"
        return GovDecision.ALLOW, "r", "ok"


def test_a_retry_after_an_approval_neither_asks_the_human_again_nor_counts_the_call_again() -> None:
    # Kills: a RETRY re-entry that loses the approval (human asked twice) or the call id (budget counts again).
    retry = _RetryWhenApproved()
    engine, inst, budget = _budget_instance(HoldEveryTool(), retry, max_gov_retries=3)
    inst.run_user_text("go")
    done = _approve(inst)
    assert retry.retried
    assert not done.awaiting_hitl and done.boundary_errors == []
    assert engine.tool_runs == ["t"] and budget.tool_calls == 1


class _RetryOnceFor:
    def __init__(self, name: str) -> None:
        self.name, self.n = name, 0

    def evaluate_egress(self, intent, *, config):  # noqa: ARG002
        if intent.op == "TOOL_CALL" and intent.tool_name == self.name:
            self.n += 1
            if self.n == 1:
                return GovDecision.RETRY, "r", "again"
        return GovDecision.ALLOW, "r", "ok"


def test_an_egress_retry_inside_a_parallel_batch_runs_every_call_and_ends_the_turn() -> None:
    # Kills: a retried call of a batch whose engine call was dropped (wait open, turn silent).
    engine = BatchEngine(["a", "b", "c"])
    inst = RuntimeInstance.from_parts(
        engine=engine,
        config=KernelConfig(egress_governance_plugin=_RetryOnceFor("b"), parallel_tool_calls=True, max_gov_retries=2),
    )
    done = inst.run_user_text("go")
    assert sorted(engine.tool_runs) == ["a", "b", "c"]
    assert [r.content for r in done.client_responses] == ["final"] and done.boundary_errors == []
    _no_waits(inst)
    assert [r.content for r in inst.run_user_text("again", turn_id="u2").client_responses]


def _transcript(inst):
    from mas.runtime.boundary.context.working_memory import WorkingMemoryStore

    wm = WorkingMemoryStore()
    wm.project_new(sorted(inst.kernel.run.events, key=lambda r: r.seq))
    declared = {c["id"]: c["function"]["name"] for m in wm.messages if m.get("tool_calls") for c in m["tool_calls"]}
    results = [(declared.get(m["tool_call_id"]), m["content"]) for m in wm.messages if m.get("role") == "tool"]
    return declared, results


def _batch_hold_instance():
    engine = BatchEngine(["read_file", "delete_file", "write_file"])
    inst = RuntimeInstance.from_parts(
        engine=engine, config=KernelConfig(egress_governance_plugin=HoldTool("delete_file"), parallel_tool_calls=True)
    )
    inst.run_user_text("go")
    return engine, inst


def test_the_calls_queued_behind_a_hold_survive_a_pause() -> None:
    # Kills: a pause dropping the queued calls of a batch (the model was asked without their results).
    engine, inst = _batch_hold_instance()
    q = inst.kernel.q
    inst.pause()
    assert [t["tool_name"] for t in q.queued_tools] == ["write_file"]
    inst.resume()
    done = _approve(inst)
    assert engine.tool_runs == ["read_file", "delete_file", "write_file"]
    assert [r.content for r in done.client_responses] == ["final"]
    declared, results = _transcript(inst)
    assert sorted(n for n, _c in results) == sorted(declared.values())  # no tool call without its result


def test_the_queue_of_a_held_batch_survives_a_checkpoint_taken_during_a_pause() -> None:
    # Kills: queued_tools or pending_tools_by_cid missing from the checkpoint of a held state.
    engine, inst = _batch_hold_instance()
    inst.pause()
    inst.kernel.restore(json.loads(json.dumps(inst.kernel.snapshot())))
    inst.resume()
    done = _approve(inst)
    assert engine.tool_runs == ["read_file", "delete_file", "write_file"]
    assert [r.content for r in done.client_responses] == ["final"]


def _mixed_holds_instance():
    engine = BatchEngine(["a", "held", "c"])
    inst = RuntimeInstance.from_parts(
        engine=engine,
        config=KernelConfig(
            egress_governance_plugin=HoldTool("held"),
            parallel_tool_calls=True,
            hitl_on_tool_result=True,
            ingress_governance_plugins=(RegisteredIngressPlugin(plugin=HoldEveryResult()),),
        ),
    )
    inst.run_user_text("go")
    return engine, inst


def _drain(inst):
    q = inst.kernel.q
    done = None
    for _ in range(8):
        if not q.hitl_holds:
            break
        done = _answer(inst, max(q.hitl_holds))
    return done


def test_mixed_egress_and_result_holds_all_run_with_and_without_a_pause() -> None:
    # Kills: a pause that loses the queued call or the held call when egress and result holds coexist.
    engine, inst = _mixed_holds_instance()
    done = _drain(inst)
    assert sorted(engine.tool_runs) == ["a", "c", "held"] and [r.content for r in done.client_responses] == ["final"]
    _no_waits(inst)
    engine, inst = _mixed_holds_instance()
    q = inst.kernel.q
    inst.pause()
    for rid in sorted(q.hitl_holds):
        _answer(inst, rid)
    inst.resume()
    done = _drain(inst) or inst.resume()
    assert sorted(engine.tool_runs) == ["a", "c", "held"]
    assert not q.hitl_holds
    _no_waits(inst)
    declared, results = _transcript(inst)
    assert sorted(n for n, _c in results) == sorted(declared.values())


def test_denying_the_held_call_drops_the_sibling_result_holds() -> None:
    # Kills: an egress denial that leaves a sibling result hold and its wait behind.
    _engine, inst = _mixed_holds_instance()
    q = inst.kernel.q
    egress_hold = next(k for k, v in q.hitl_holds.items() if not v.is_ingress)
    _answer(inst, egress_hold, HitlResolveChoice.BLOCK)
    assert q.hitl_holds == {}
    _no_waits(inst)


def test_result_holds_answered_in_reverse_order_while_paused_release_every_result_once() -> None:
    # Kills: a release that appends a result twice, leaves a hold behind, or (when the pause forgets which call
    # each parked result answers) binds the results to the wrong calls.
    engine, inst = _batch_with_result_holds(["alpha", "beta"])
    inst.run_user_text("go")
    q = inst.kernel.q
    ids = sorted(q.hitl_holds)
    inst.pause()
    _answer(inst, ids[1])
    inst.resume()  # beta alone is released first, then alpha is answered live
    done = _answer(inst, ids[0])
    assert [r.content for r in done.client_responses] == ["final"]
    assert q.hitl_holds == {}
    _no_waits(inst)
    assert len([e for e in inst.kernel.run.events if e.response_kind == "TOOL_RESULT"]) == 2
    _declared, results = _transcript(inst)
    assert all(content.startswith(name) for name, content in results), results
    named = {r.tool_name for r in inst.kernel.run.events if r.response_kind == "TOOL_RESULT"}
    assert named == {"alpha", "beta"}  # each released row still names its call after the pause


def test_a_result_hold_denied_while_paused_ends_the_turn_cleanly_on_resume() -> None:
    # Kills: a denial among several answers during a pause leaving holds or waits.
    _engine, inst = _batch_with_result_holds(["alpha", "beta"])
    inst.run_user_text("go")
    q = inst.kernel.q
    ids = sorted(q.hitl_holds)
    inst.pause()
    _answer(inst, ids[0])
    _answer(inst, ids[1], HitlResolveChoice.BLOCK)
    inst.resume()
    assert q.hitl_holds == {}
    _no_waits(inst)


def test_results_released_out_of_order_are_bound_to_their_own_tool_call() -> None:
    # Kills: binding a result to the first open tool call by position (beta's answer landed on alpha's call).
    _engine, inst = _batch_with_result_holds(["alpha", "beta"])
    inst.run_user_text("go")
    ids = sorted(inst.kernel.q.hitl_holds)
    _answer(inst, ids[1])
    _answer(inst, ids[0])
    _declared, results = _transcript(inst)
    assert len(results) == 2
    assert all(content.startswith(name) for name, content in results), results


def test_a_model_call_hold_survives_a_pause_and_runs_once_after_the_approval() -> None:
    # Kills: a held model call lost, or run twice, across a pause.
    class HoldLLMOnce:
        n = 0

        def evaluate_egress(self, intent, *, config):  # noqa: ARG002
            if intent.op == "LLM_CALL" and not intent.hitl_approved:
                self.n += 1
                if self.n == 1:
                    return GovDecision.HITL, "h", "hold the model call"
            return GovDecision.ALLOW, "h", "ok"

    engine = ScriptedEngine([])
    inst = RuntimeInstance.from_parts(engine=engine, config=KernelConfig(egress_governance_plugin=HoldLLMOnce()))
    assert inst.run_user_text("go").awaiting_hitl
    inst.pause()
    _answer(inst, gov_first_hold_id(inst.kernel.q))
    done = inst.resume()
    assert engine.llm_calls == 1 and [r.content for r in done.client_responses] == ["answer-1"]


def _fail_once(inst, when) -> None:
    real = inst.kernel.plugin.handle_event
    state = {"armed": True}

    def failing(q, run, event, *, config):
        if state["armed"] and when(event):
            state["armed"] = False
            raise RuntimeError("pattern bug")
        return real(q, run, event, config=config)

    inst.kernel.plugin.handle_event = failing


@pytest.mark.parametrize("kind", ["tool-result", "ctx-assembly"])
def test_a_pattern_that_raises_in_a_turn_in_progress_ends_that_turn_and_the_next_one_runs(kind: str) -> None:
    # Kills: the rollback reopening the wait of a call the engine already answered, wedging the session.
    from mas.runtime.schema.ingress import CtxAssemblyComplete

    inst = RuntimeInstance.from_parts(engine=ScriptedEngine(["t"]), config=KernelConfig())
    if kind == "tool-result":
        _fail_once(inst, lambda e: isinstance(e, EngineIoReturn) and e.response_kind == "TOOL_RESULT")
    else:
        _fail_once(inst, lambda e: isinstance(e, CtxAssemblyComplete))
    failed = inst.run_user_text("go")
    assert [e.code for e in failed.boundary_errors] == ["PLUGIN_ERROR"]
    q = inst.kernel.q
    assert q.dp is DpState.IDLE
    _no_waits(inst)
    again = inst.run_user_text("again", turn_id="u2")
    assert again.boundary_errors == [] and again.client_responses


def test_a_pattern_error_while_a_hold_is_pending_keeps_the_hold() -> None:
    # Kills: ending a failed turn without keep_holds, which dropped a human question still pending.
    engine = BatchEngine(["a", "held"])
    inst = RuntimeInstance.from_parts(
        engine=engine, config=KernelConfig(egress_governance_plugin=HoldTool("held"), parallel_tool_calls=True)
    )
    _fail_once(inst, lambda e: isinstance(e, EngineIoReturn) and e.response_kind == "TOOL_RESULT")
    failed = inst.run_user_text("go")
    assert [e.code for e in failed.boundary_errors] == ["PLUGIN_ERROR"]
    assert len(inst.kernel.q.hitl_holds) == 1 and failed.awaiting_hitl


def test_a_checkpoint_with_fields_this_version_does_not_know_is_refused_not_trimmed() -> None:
    # Kills: HitlHold(**row) failing with a bare TypeError, or unknown top-level fields silently dropped.
    from mas.runtime.kernel.state_serialize import CheckpointVersionError, q_product_from_dict

    _engine, inst = _held_tool()
    data = json.loads(json.dumps(inst.kernel.snapshot()))["q"]
    newer_hold = json.loads(json.dumps(data))
    next(iter(newer_hold["hitl_holds"].values()))["new_field"] = 1
    with pytest.raises(CheckpointVersionError, match="new_field"):
        q_product_from_dict(newer_hold)
    with pytest.raises(CheckpointVersionError, match="new_state"):
        q_product_from_dict({**data, "new_state": 1})
    retired = {**data, "hitl_request_id": 7, "hitl_pending_ingress": ""}
    assert q_product_from_dict(retired).hitl_holds.keys() == inst.kernel.q.hitl_holds.keys()


def test_a_pause_answers_the_tool_calls_it_interrupts_as_unknown_not_as_a_result() -> None:
    # Kills: a pause that leaves an interrupted tool call without an answer, or answers it as a normal result
    # ("cancelled") although a running tool is not stopped, so a pattern result list or the model takes it as
    # the outcome and may repeat a call that already executed.
    from mas.runtime.kernel.inflight import register_inflight
    from mas.runtime.schema.ingress import LifecyclePause

    inst = RuntimeInstance.from_parts(engine=ScriptedEngine(), config=KernelConfig())
    kernel = inst.kernel
    cid = kernel.run.allocate_correlation_id()
    register_inflight(kernel.q, cid, kind="TOOL", op="TOOL_CALL")
    kernel.q.pending_tools_by_cid[cid] = ("slow_tool", {"n": 1})
    kernel.q.call_slots[cid] = "call_1_0"
    kernel.transition(LifecyclePause())
    (row,) = [r for r in kernel.run.events if r.correlation_id == cid]
    assert (row.response_kind, row.tool_name, row.tool_arguments, row.call_id, row.placeholder) == (
        "ERROR",
        "slow_tool",
        {"n": 1},
        "call_1_0",
        True,
    )
    assert "may have executed" in row.text and "unknown" in row.text
    assert cid not in kernel.q.pending_tools_by_cid and kernel.q.outbound_waits == []


# --- Round 5: approvals without an id, interrupted calls, identical calls, skips in a batch, pause races ---


class ArgBatch(SimulatedEngine):
    """The model asks for calls (name, arguments) in one reply, then answers; a result names its call and id."""

    def __init__(self, calls, rounds: int = 1) -> None:
        super().__init__()
        self.calls, self.rounds, self.n = calls, rounds, 0
        self.runs: list[tuple[int, tuple]] = []
        self.by_cid: dict[int, tuple] = {}
        self.sched: tuple | None = None

    def set_tool_for_correlation(self, cid, name, args):
        self.by_cid[cid] = (name, dict(args or {}))

    def set_scheduled_tool(self, name, args):
        self.sched = (name, dict(args or {}))

    async def ainvoke(self, io):
        if io.op == "LLM_CALL":
            self.n += 1
            if self.n <= self.rounds:
                return _ret(
                    io,
                    next_step="PARALLEL_TOOL_CALLS",
                    parallel_tools=tuple(ToolCallSpec(tool_name=n, tool_arguments=a) for n, a in self.calls),
                    offered_tools=sorted({n for n, _ in self.calls}),
                )
            return _ret(io, text=f"final-{self.n}")
        call = self.by_cid.get(io.correlation_id) or self.sched
        self.runs.append((io.correlation_id, call))
        text = f"R(cid={io.correlation_id},{call[0]},{json.dumps(call[1])})"
        return _ret(io, response_kind="TOOL_RESULT", next_step="LLM_CALL", text=text)


def _declared_binds(inst):
    from mas.runtime.boundary.context.working_memory import WorkingMemoryStore

    wm = WorkingMemoryStore()
    wm.project_new(sorted(inst.kernel.run.events, key=lambda r: r.seq))
    order = [c["id"] for m in wm.messages if m.get("tool_calls") for c in m["tool_calls"]]
    by_id = {m["tool_call_id"]: m["content"] for m in wm.messages if m.get("role") == "tool"}
    return [by_id.get(i) for i in order]


def test_a_bare_approval_is_refused_when_several_requests_are_pending() -> None:
    # Kills: HitlApprove approving the oldest hold when several coexist (here delete_all, never looked at).
    engine = ArgBatch([("read", {}), ("delete_all", {}), ("x", {})])
    inst = RuntimeInstance.from_parts(
        engine=engine,
        config=KernelConfig(
            egress_governance_plugin=HoldTool("delete_all"),
            parallel_tool_calls=True,
            hitl_on_tool_result=True,
            ingress_governance_plugins=(RegisteredIngressPlugin(plugin=HoldEveryResult()),),
        ),
    )
    inst.run_user_text("go")
    q = inst.kernel.q
    assert len(q.hitl_holds) > 1
    before = dict(q.hitl_holds)
    refused = inst.feed(HitlApprove())
    assert [e.code for e in refused.boundary_errors] == ["HITL_APPROVE_AMBIGUOUS"]
    assert q.hitl_holds.keys() == before.keys()
    assert all(call[0] != "delete_all" for _cid, call in engine.runs)


def test_a_bare_approval_while_paused_is_not_stored_when_several_requests_are_pending() -> None:
    # Kills: a deferred bare approval landing on the oldest hold.
    _engine, inst = _mixed_holds_instance()
    q = inst.kernel.q
    inst.pause()
    inst.feed(HitlApprove())
    assert all(not h.deferred for h in q.hitl_holds.values())


def test_a_bare_approval_still_answers_the_one_pending_request() -> None:
    # Kills: refusing the plain case where exactly one request is pending.
    engine, inst = _held_tool()
    inst.feed(HitlApprove())
    assert engine.tool_runs == ["t"]


def test_a_late_result_for_an_interrupted_call_is_dropped_and_does_not_disturb_the_turn() -> None:
    # Kills: the real result of a call the pause already answered being appended (two rows for one call) or
    # treated as a stale reply that tears down the waits and the hold of the turn in progress.
    from mas.runtime.schema.ingress import LifecyclePause

    engine = ArgBatch([("a", {}), ("held", {})])
    inst = RuntimeInstance.from_parts(
        engine=engine, config=KernelConfig(egress_governance_plugin=HoldTool("held"), parallel_tool_calls=True)
    )
    kernel = inst.kernel
    real = kernel.transition
    state = {"paused": False}

    def pausing(event):
        result = real(event)
        if not state["paused"] and kernel.q.hitl_holds:
            state["paused"] = True
            real(LifecyclePause())
        return result

    kernel.transition = pausing
    inst.run_user_text("go")
    kernel.transition = real
    inst.resume()
    (row,) = [r for r in kernel.run.events if r.tool_name == "a"]
    before = (len(kernel.run.events), dict(kernel.q.hitl_holds), list(kernel.q.outbound_waits))
    kernel.transition(
        EngineIoReturn(
            correlation_id=row.correlation_id, response_kind="TOOL_RESULT", next_step="LLM_CALL", text="late"
        )
    )
    assert (len(kernel.run.events), dict(kernel.q.hitl_holds), list(kernel.q.outbound_waits)) == before
    assert [r.text for r in kernel.run.events if r.correlation_id == row.correlation_id] == [row.text]


def test_the_result_of_an_approved_held_call_is_bound_to_its_own_call() -> None:
    # Kills: a held call that loses its declared id, so its result (released first) binds to the first open call.
    engine = ArgBatch([("a", {}), ("held", {})])
    inst = RuntimeInstance.from_parts(
        engine=engine,
        config=KernelConfig(
            egress_governance_plugin=HoldTool("held"),
            parallel_tool_calls=True,
            hitl_on_tool_result=True,
            ingress_governance_plugins=(RegisteredIngressPlugin(plugin=HoldEveryResult()),),
        ),
    )
    inst.run_user_text("go")
    q = inst.kernel.q
    egress = next(k for k, v in q.hitl_holds.items() if not v.is_ingress)
    a_result = next(k for k, v in q.hitl_holds.items() if v.is_ingress)
    _answer(inst, egress)
    held_result = next(k for k, v in q.hitl_holds.items() if v.is_ingress and k != a_result)
    _answer(inst, held_result)  # the approved call's result is released before the first call's
    _answer(inst, a_result)
    first, second = _declared_binds(inst)
    assert ",a," in first and ",held," in second


def test_a_pause_while_a_tool_runs_records_its_outcome_as_unknown_and_drops_the_late_result() -> None:
    # Kills: telling the model a running tool was "cancelled" (it ran), using a normal result row for it, or
    # appending its real result later (two rows for one call).
    import asyncio

    class Slow(ArgBatch):
        def __init__(self, calls) -> None:
            super().__init__(calls)
            self.gate, self.started, self.side_effects = None, None, []

        async def ainvoke(self, io):
            call = self.by_cid.get(io.correlation_id) or self.sched
            if io.op == "TOOL_CALL" and call and call[0] == "transfer":
                self.started.set()
                await self.gate.wait()
                self.side_effects.append(io.correlation_id)
            return await super().ainvoke(io)

    async def scenario():
        engine = Slow([("transfer", {"amt": 100}), ("held", {})])
        engine.gate, engine.started = asyncio.Event(), asyncio.Event()
        inst = RuntimeInstance.from_parts(
            engine=engine, config=KernelConfig(egress_governance_plugin=HoldTool("held"), parallel_tool_calls=True)
        )
        run = asyncio.create_task(inst.arun_user_text("go"))
        await asyncio.wait_for(engine.started.wait(), 5)
        await inst.apause()
        engine.gate.set()
        await asyncio.wait_for(run, 5)
        await inst.aresume()
        done = await inst.afeed(
            HitlResolve(request_id=gov_first_hold_id(inst.kernel.q), resolution=HitlResolveChoice.ALLOW)
        )
        return engine, inst, done

    engine, inst, done = asyncio.run(scenario())
    rows = [r for r in inst.kernel.run.events if r.tool_name == "transfer"]
    assert engine.side_effects and len(rows) == 1  # it ran, and the ledger has one row for it
    (row,) = rows
    assert (row.response_kind, row.placeholder) == ("ERROR", True)
    assert "may have executed" in row.text and "cancelled" not in row.text.lower()
    assert [r.content for r in done.client_responses] == ["final-2"]
    binds = _declared_binds(inst)
    assert binds[0] == row.text and binds[1].startswith("R(")
    _no_waits(inst)


@pytest.mark.parametrize("decision", [GovDecision.SKIP, GovDecision.BLACKLIST, GovDecision.BLOCK])
def test_a_skipped_or_denied_call_of_a_batch_is_answered_on_its_own_call(decision) -> None:
    # Kills: a skip placeholder logged without its call id, so it took the first open call and shifted the results.
    class Verdict:
        def evaluate_egress(self, intent, *, config):  # noqa: ARG002
            if intent.op == "TOOL_CALL" and intent.tool_name == "b":
                return decision, "p", "no"
            return GovDecision.ALLOW, "p", "ok"

    engine = ArgBatch([("a", {}), ("b", {}), ("c", {})])
    inst = RuntimeInstance.from_parts(
        engine=engine, config=KernelConfig(egress_governance_plugin=Verdict(), parallel_tool_calls=True)
    )
    inst.run_user_text("go")
    first, second, third = _declared_binds(inst)
    assert first.startswith("R(cid=") and ",a," in first
    assert ",b," not in (second or "") and not second.startswith("R(")
    assert ",c," in third


def _identical_result_holds():
    engine = ArgBatch([("now", {}), ("now", {})])
    inst = RuntimeInstance.from_parts(
        engine=engine,
        config=KernelConfig(
            parallel_tool_calls=True,
            hitl_on_tool_result=True,
            ingress_governance_plugins=(RegisteredIngressPlugin(plugin=HoldEveryResult()),),
        ),
    )
    inst.run_user_text("go")
    return engine, inst


@pytest.mark.parametrize("reverse", [False, True])
def test_identical_calls_get_their_own_results_in_either_release_order(reverse: bool) -> None:
    # Kills: binding a result by tool name and arguments, which cannot tell identical calls apart.
    engine, inst = _identical_result_holds()
    ids = sorted(inst.kernel.q.hitl_holds)
    for rid in reversed(ids) if reverse else ids:
        _answer(inst, rid)
    first, second = _declared_binds(inst)
    cids = sorted(cid for cid, _call in engine.runs)
    assert first.startswith(f"R(cid={cids[0]},") and second.startswith(f"R(cid={cids[1]},")


def test_skipping_the_second_of_two_identical_calls_lands_on_the_second() -> None:
    # Kills: an operator SKIP of the second identical call being bound to the first.
    engine, inst = _identical_result_holds()
    ids = sorted(inst.kernel.q.hitl_holds)
    _answer(inst, ids[1], HitlResolveChoice.SKIP)
    _answer(inst, ids[0])
    first, second = _declared_binds(inst)
    cids = sorted(cid for cid, _call in engine.runs)
    assert first.startswith(f"R(cid={cids[0]},") and not second.startswith("R(")


def test_a_pause_that_lands_while_the_driver_is_between_steps_does_not_dispatch_the_cancelled_call() -> None:
    # Kills: the driver registering a call's wait itself after an await, re-adding it behind a pause (the call
    # was dispatched while paused and the turn stalled after resume).
    from mas.runtime.schema.ingress import LifecyclePause

    engine = ArgBatch([("a", {}), ("held", {}), ("c", {})])
    inst = RuntimeInstance.from_parts(
        engine=engine, config=KernelConfig(egress_governance_plugin=HoldTool("held"), parallel_tool_calls=True)
    )
    kernel = inst.kernel
    real = kernel.transition
    state = {"paused": False}

    def pausing(event):
        result = real(event)
        if not state["paused"] and kernel.q.hitl_holds:
            state["paused"] = True
            real(LifecyclePause())  # lands right after the step that sent the batch
        return result

    kernel.transition = pausing
    inst.run_user_text("go")
    kernel.transition = real
    assert engine.runs == []  # `a` was answered as interrupted, not sent
    assert [w.kind for w in kernel.q.outbound_waits] == ["HITL"]
    inst.resume()
    done = _answer(inst, gov_first_hold_id(kernel.q))
    assert [name for _cid, (name, _a) in engine.runs] == ["held", "c"]
    assert [r.content for r in done.client_responses] == ["final-2"]
    _no_waits(inst)


# --- Checkpoints written by the earlier tips (one hold slot, a session-wide blacklist flag) ----------------

_LEGACY = json.loads(
    (__import__("pathlib").Path(__file__).parent / "data" / "legacy_checkpoints_27744303.json").read_text()
)


def _legacy_instance(name: str):
    inst = RuntimeInstance.from_parts(engine=ScriptedEngine(["lookup"]), config=KernelConfig())
    inst.kernel.restore(copy.deepcopy(_LEGACY[name]))
    return inst


def test_an_older_idle_checkpoint_loads_and_the_session_goes_on() -> None:
    # Kills: the retired fields making every older checkpoint unloadable.
    inst = _legacy_instance("idle")
    assert inst.kernel.q.hitl_holds == {} and inst.kernel.q.gov_state == "IDLE"
    assert [r.content for r in inst.run_user_text("again", turn_id="u9").client_responses]


def test_an_older_checkpoint_taken_at_a_hold_keeps_the_question_under_its_request_id() -> None:
    # Kills: a hold lost in the migration (HITL_PENDING with nobody to answer) or answered under a new id.
    request_id = _LEGACY["egress_hold"]["q"]["hitl_request_id"]
    inst = _legacy_instance("egress_hold")
    q = inst.kernel.q
    assert list(q.hitl_holds) == [request_id] and q.gov_state == "HITL_PENDING"
    hold = q.hitl_holds[request_id]
    assert (hold.pending_schedule, hold.tool_name, hold.tool_arguments) == ("TOOL_CALL", "lookup", {"k": 1})
    done = _answer(inst, request_id)
    assert done.client_responses and done.boundary_errors == []
    assert q.hitl_holds == {}
    _no_waits(inst)


def test_an_older_checkpoint_with_a_session_wide_blacklist_is_refused() -> None:
    # Kills: loading it with the blacklist silently dropped (tools the session had blacklisted would run).
    from mas.runtime.kernel.state_serialize import CheckpointVersionError

    with pytest.raises(CheckpointVersionError, match="blacklist"):
        _legacy_instance("blacklisted")


def test_an_older_checkpoint_taken_while_a_result_was_held_is_refused() -> None:
    # Kills: loading it into HITL_PENDING with no hold: the earlier versions did not save the held result.
    from mas.runtime.kernel.state_serialize import CheckpointVersionError

    with pytest.raises(CheckpointVersionError, match="did not save the result"):
        _legacy_instance("result_hold")


def test_an_older_checkpoint_that_saved_the_held_result_is_rebuilt_as_a_result_hold() -> None:
    # Kills: a migration that drops a parked result it was given.
    from mas.runtime.kernel.state_serialize import q_product_from_dict

    old = json.loads(json.dumps(_LEGACY["egress_hold"]["q"]))
    request_id = old["hitl_request_id"]
    old["hitl_pending_schedule"] = "NONE"
    old["pending_ingress_return"] = {
        "correlation_id": 1,
        "response_kind": "TOOL_RESULT",
        "next_step": "LLM_CALL",
        "text": "r",
    }
    (hold,) = q_product_from_dict(old).hitl_holds.values()
    assert hold.is_ingress and hold.request_id == request_id


def test_a_state_that_cannot_be_copied_does_not_stop_the_kernel(caplog) -> None:
    # Kills: a design pattern keeping a lock (or any object deepcopy refuses) in dp_data making every step raise.
    import threading

    inst = RuntimeInstance.from_parts(engine=ScriptedEngine(), config=KernelConfig())
    inst.kernel.q.dp_data["handle"] = threading.Lock()
    with caplog.at_level("WARNING"):
        first = inst.run_user_text("one")
        second = inst.run_user_text("two", turn_id="u2")
    assert [r.content for r in first.client_responses] == ["answer-1"]
    assert [r.content for r in second.client_responses] == ["answer-2"]
    assert sum("cannot be copied" in rec.message for rec in caplog.records) == 1  # said once, not per step


def test_the_snapshot_of_a_step_is_not_shared_with_a_nested_step() -> None:
    # Kills: the snapshot kept on the kernel, so a transition inside a transition restored the wrong state.
    from mas.runtime.schema.ingress import UserInputReceived

    inst = RuntimeInstance.from_parts(engine=ScriptedEngine(), config=KernelConfig())
    kernel = inst.kernel
    inst.run_user_text("one")
    inner_done = []

    class Nested:
        def __init__(self, real):
            self.real = real

        def handle_event(self, q, run, event, *, config):
            if not inner_done:
                inner_done.append(True)
                q.hitl_tools_approved_turn = True  # a write of the outer step, before the nested one runs
                kernel.transition(UserInputReceived(user_turn_id="inner", text="inner"))
            raise RuntimeError("outer step fails after the nested one")

        def __getattr__(self, name):
            return getattr(self.real, name)

    real_plugin = kernel.plugin
    kernel.plugin = Nested(real_plugin)
    out = kernel.transition(UserInputReceived(user_turn_id="outer", text="outer"))
    kernel.plugin = real_plugin
    assert any(getattr(sym, "code", "") == "PLUGIN_ERROR" for sym in out.egress)
    assert kernel.q.hitl_tools_approved_turn is False  # the outer snapshot, not the nested step's, was restored


def test_a_restored_deterministic_pattern_reads_only_this_turns_results() -> None:
    # Kills: ledger_mark living only on the plugin instance, so a restored kernel (new plugin, same
    # checkpoint) sees every earlier TOOL_RESULT and a pattern that indexes the current turn raises.
    from mas.library.standard.plugins.design_patterns.linear import DeterministicLinearPlugin
    from mas.runtime.kernel.state import RunEvent
    from mas.runtime.kernel.state_serialize import q_product_from_dict, q_product_to_dict, run_from_dict, run_to_dict

    q = QProduct()
    run = RunLedger()
    run.append(RunEvent(correlation_id=1, response_kind="TOOL_RESULT", next_step="LLM_CALL", text="old"))
    q.dp_data["ledger_mark"] = run.max_seq()
    run.append(RunEvent(correlation_id=2, response_kind="TOOL_RESULT", next_step="LLM_CALL", text="new"))
    q2 = q_product_from_dict(json.loads(json.dumps(q_product_to_dict(q))))
    run2 = run_from_dict(json.loads(json.dumps(run_to_dict(run))))
    fresh = DeterministicLinearPlugin()
    assert fresh._extract_tool_results(run2, q2) == ["new"]
