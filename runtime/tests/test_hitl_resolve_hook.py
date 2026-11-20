#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A governance plugin can refuse a HITL answer that did not verify, before the call starts."""

from __future__ import annotations

import pytest
from mas.runtime.boundary.gov.plugin import GovernancePluginChain
from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.engine.simulated import SimulatedEngine
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.coupling import GovDecision
from mas.runtime.kernel.egress_gate import authorize_scheduled_egress
from mas.runtime.kernel.state import QProduct, RunLedger
from mas.runtime.schema.hitl import HitlResolveChoice
from mas.runtime.schema.ingress import EngineIoReturn, HitlResolve, ToolCallSpec

_REASON = "the approval did not verify"


class _Engine(SimulatedEngine):
    """The model asks for two tools in a row, then answers."""

    def __init__(self, *, plan_as_text: bool = False) -> None:
        super().__init__()
        self.tools_started: list[str] = []
        self._asked = False
        self._plan_as_text = plan_as_text

    async def ainvoke(self, io):
        if io.op == "LLM_CALL" and not self._asked and self._plan_as_text:
            self._asked = True
            plan = '{"plan": [{"tool": "alpha", "arguments": {"n": 1}}, {"tool": "beta", "arguments": {"n": 2}}]}'
            return EngineIoReturn(
                correlation_id=io.correlation_id,
                response_kind="MODEL_TEXT",
                next_step="STOP",
                text=plan,
                offered_tools=["alpha", "beta"],
            )
        if io.op == "LLM_CALL" and not self._asked:
            self._asked = True
            return EngineIoReturn(
                correlation_id=io.correlation_id,
                response_kind="MODEL_TEXT",
                next_step="PARALLEL_TOOL_CALLS",
                parallel_tools=(
                    ToolCallSpec(tool_name="alpha", tool_arguments={"n": 1}),
                    ToolCallSpec(tool_name="beta", tool_arguments={"n": 2}),
                ),
                offered_tools=["alpha", "beta"],
            )
        if io.op == "LLM_CALL":
            return EngineIoReturn(
                correlation_id=io.correlation_id, response_kind="MODEL_TEXT", next_step="STOP", text="done"
            )
        self.tools_started.append(str(io.correlation_id))
        return EngineIoReturn(
            correlation_id=io.correlation_id, response_kind="TOOL_RESULT", next_step="LLM_CALL", text="ran"
        )


class _Holds:
    """Asks a human for every tool call. Optionally checks the answer."""

    def __init__(self, verify=None) -> None:
        self.asked = 0
        self.seen: list[tuple] = []
        if verify is not None:
            self.evaluate_hitl_resolve = self._verify
            self._verify_fn = verify

    def evaluate_egress(self, intent, *, config):  # noqa: ANN001, ARG002
        if intent.op == "TOOL_CALL":
            self.asked += 1
            return GovDecision.HITL, "holds", "needs a human"
        return GovDecision.ALLOW, "holds", "ok"

    def _verify(self, request, resolve):
        self.seen.append((request, resolve))
        return self._verify_fn(request, resolve)


class _Allow:
    def __init__(self) -> None:
        self.asked = 0

    def resolve(self, request):
        self.asked += 1
        return HitlResolve(
            request_id=request.request_id,
            resolution=HitlResolveChoice.ALLOW,
            operator_context={"operator_id": "op", "proof": "tok-123"},
        )


def _run(plugin, *, once_per_turn: bool = False):
    engine = _Engine()
    inst = RuntimeInstance.from_parts(
        engine=engine,
        config=KernelConfig(
            parallel_tool_calls=False, hitl_once_per_turn=once_per_turn, egress_governance_plugin=plugin
        ),
    )
    inst.driver.hitl = _Allow()
    inst.capture_session_baseline()
    trace = inst.run_user_text("go")
    return engine, inst, trace


def _results(inst) -> list[str]:
    return [ev.text or "" for ev in inst.kernel.run.events if ev.response_kind == "TOOL_RESULT"]


def test_refusing_hook_turns_an_allow_answer_into_block_and_the_tool_never_starts() -> None:
    # Kills: dropping the hook call in apply_hitl_resolve (the tool would start).
    plugin = _Holds(lambda req, res: (GovDecision.BLOCK, "id", _REASON))
    engine, inst, _ = _run(plugin)
    assert engine.tools_started == []
    assert any(_REASON in text for text in _results(inst))
    request, resolve = plugin.seen[0]
    assert request.context_data["tool"] == "alpha"
    assert request.context_data["arguments"] == {"n": 1}
    assert resolve.operator_context["proof"] == "tok-123"


def test_hook_without_opinion_leaves_the_allow_in_force() -> None:
    # Kills: treating None (or a plugin with no hook) as a refusal.
    for plugin in (_Holds(lambda req, res: None), _Holds()):
        engine, _, _ = _run(plugin)
        assert len(engine.tools_started) == 2


def test_hook_that_raises_blocks_without_leaking_the_error() -> None:
    # Kills: letting the exception escape, or swallowing it as "no opinion".
    def boom(req, res):
        raise RuntimeError("secret-detail-xyz")

    engine, inst, _ = _run(_Holds(boom))
    assert engine.tools_started == []
    assert not any("secret-detail-xyz" in text for text in _results(inst))


def test_malformed_verdict_blocks() -> None:
    # Kills: trusting any non-None return as an allow.
    engine, _, _ = _run(_Holds(lambda req, res: "nope"))
    assert engine.tools_started == []


def test_chain_first_deny_in_order_wins_and_a_later_plugin_is_not_asked() -> None:
    # Kills: last-wins, or consulting every plugin after a deny.
    calls: list[str] = []

    def deny(name):
        def verify(req, res):
            calls.append(name)
            return GovDecision.BLOCK, name, f"{name} refused"

        return verify

    first, second = _Holds(deny("first")), _Holds(deny("second"))
    chain = GovernancePluginChain([first, second])
    verdict = chain.evaluate_hitl_resolve(_request(), _Allow().resolve(_request()))
    assert verdict is not None and verdict[1] == "first"
    assert calls == ["first"]
    calls.clear()
    chain = GovernancePluginChain([_Holds(lambda r, s: None), _Holds(deny("second"))])
    verdict = chain.evaluate_hitl_resolve(_request(), _Allow().resolve(_request()))
    assert verdict is not None and verdict[1] == "second"


def _request():
    from mas.runtime.schema.egress import EmitHitlRequest

    return EmitHitlRequest(request_id=1, pending_schedule="TOOL_CALL", context_data={"op": "TOOL_CALL"})


def test_the_kernel_uses_the_chain_hook() -> None:
    # Kills: calling the hook on a single plugin only, not on a chain.
    chain = GovernancePluginChain([_Holds(), _Holds(lambda r, s: (GovDecision.BLOCK, "x", _REASON))])
    engine, _, _ = _run(chain)
    assert engine.tools_started == []


def test_approval_covers_one_call_by_default_and_the_whole_turn_when_asked() -> None:
    # Kills: leaking one approval to the next call (default), or losing hitl_once_per_turn.
    engine, inst, _ = _run(_Holds())
    assert (inst.driver.hitl.asked, len(engine.tools_started)) == (2, 2)
    engine, inst, _ = _run(_Holds(), once_per_turn=True)
    assert (inst.driver.hitl.asked, len(engine.tools_started)) == (1, 2)


def test_an_approval_not_used_by_its_call_is_not_left_for_the_next_one() -> None:
    # Kills: an early return in egress authorization leaving hitl_gov_override set.
    q = QProduct()
    q.scheduled_egress = "TOOL_CALL"
    q.tool_blacklist = ["delete"]
    q.pending_tool_name = "delete"
    q.hitl_gov_override = True
    authorize_scheduled_egress(q, RunLedger(), KernelConfig())
    assert q.hitl_gov_override is False


@pytest.mark.parametrize("pattern", ["react", "cot", "plan_execute", "introspection", "tree_of_thoughts"])
def test_turn_wide_approvals_do_not_outlive_the_turn(pattern: str) -> None:
    # Kills: not resetting hitl_tools_approved_turn at the next user input (turn 2 would skip the hold);
    # before the reset moved to the kernel only the react pattern did it.
    plugin = _Holds()
    engine = _Engine(plan_as_text=pattern == "plan_execute")
    inst = RuntimeInstance.from_parts(
        engine=engine,
        config=KernelConfig(
            pattern_plugin_id=pattern,
            parallel_tool_calls=False,
            hitl_once_per_turn=True,
            egress_governance_plugin=plugin,
        ),
    )
    inst.driver.hitl = _Allow()
    inst.capture_session_baseline()
    inst.run_user_text("one")
    assert inst.driver.hitl.asked == 1
    engine._asked = False
    inst.run_user_text("two")
    assert inst.driver.hitl.asked == 2
    assert not inst.kernel.q.hitl_block_committed
