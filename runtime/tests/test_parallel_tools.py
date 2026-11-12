#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Regression: schedule_parallel_tools_egress siblings all share one parent call."""

from __future__ import annotations

from dataclasses import dataclass, field

from mas.runtime.boundary.obs.observability_plugin import ObservabilityPlugin
from mas.runtime.boundary.obs.operator import ObservabilityOperator
from mas.runtime.boundary.obs.transition import TransitionEvent
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.coupling import GovDecision
from mas.runtime.kernel.inflight import pending_for_validate
from mas.runtime.kernel.ingress_step import apply_engine_io_return
from mas.runtime.kernel.parallel_tools import schedule_parallel_tools_egress
from mas.runtime.kernel.runtime_context import runtime_binding
from mas.runtime.kernel.state import QProduct, RunLedger
from mas.runtime.schema.egress import InvokeEngineIo, RaiseBoundaryError
from mas.runtime.schema.ingress import EngineIoReturn, ToolCallSpec


@dataclass
class _CapturePlugin(ObservabilityPlugin):
    seen: list[TransitionEvent] = field(default_factory=list)

    def on_transition(self, event: TransitionEvent) -> None:
        self.seen.append(event)


def test_schedule_parallel_tools_egress_siblings_share_one_parent() -> None:
    """Regression for the real kernel-driven path (not just synthetic operator
    calls): schedule_parallel_tools_egress loops synchronously over all N
    tools, and each iteration's emit_scheduled_egress steps CONTRACT_START for
    that tool BEFORE the next iteration runs. Without begin_sibling_batch
    snapshotting the enclosing frame once up front, each sibling's
    contract_call/start reads whatever the PREVIOUS sibling just pushed as its
    parent, chaining them onto each other instead of the frame that actually
    encloses the whole batch.
    """
    op = ObservabilityOperator()
    cap = _CapturePlugin()
    op.subscribe(cap)
    op.set_context(agent_id="agent-1", run_id="run-1")
    # The enclosing execution frame (e.g. the agent turn) active before the
    # model requests N parallel tool calls.
    op.push_call_frame("exec-001")

    q = QProduct()
    run = RunLedger()
    config = KernelConfig(parallel_tool_calls=True)
    tools = [ToolCallSpec(tool_name=f"tool_{i}", tool_arguments={"i": i}) for i in range(3)]

    with runtime_binding(None, op):
        out = schedule_parallel_tools_egress(q, run, config, tools)

    # Sanity: all 3 were allowed through egress governance as TOOL_CALL.
    assert len(out) == 3
    assert all(isinstance(sym, InvokeEngineIo) and sym.op == "TOOL_CALL" for sym in out)

    starts = [
        e
        for e in cap.seen
        if e.attributes.get("activity") == "contract_call"
        and e.attributes.get("boundary") == "start"
        and e.attributes.get("op") == "TOOL_CALL"
    ]
    assert len(starts) == 3

    # The regression: every sibling's contract_call/start must resolve to the
    # SAME parent (the frame active before the batch), not chain onto the
    # previously-opened sibling.
    parents = {e.parent_call_id for e in starts}
    assert parents == {"exec-001"}, parents

    # Correlate "which tool" <-> "which start event" via correlation_id,
    # shared between the returned InvokeEngineIo symbols, q.pending_tools_by_cid,
    # and TransitionEvent.correlation_id.
    tool_name_by_cid = {sym.correlation_id: q.pending_tools_by_cid[sym.correlation_id][0] for sym in out}
    starts_by_cid = {e.correlation_id: e for e in starts}
    assert set(tool_name_by_cid) == set(starts_by_cid)
    for cid, tool_name in tool_name_by_cid.items():
        assert starts_by_cid[cid].parent_call_id == "exec-001", (cid, tool_name)

    # ── Ingress half: every sibling's own result must close its own frame ────
    # Regression: apply_engine_io_return used to derive scheduled_op from the
    # shared q.tool.value, which tool_on_ingress flips EXECUTING -> DONE as
    # soon as the FIRST sibling's result commits. Every result after the
    # first then read q.tool as DONE and resolved scheduled_op to "LLM_CALL"
    # instead of "TOOL_CALL", so CONTRACT_END's (correlation_id, op)-keyed
    # lookup in ObservabilityOperator missed for every sibling but the first —
    # those frames never popped, silently leaking on the call-frame stack and
    # corrupting parent_call_id for every later, unrelated call.
    for sym in out:
        event = EngineIoReturn(
            correlation_id=sym.correlation_id,
            response_kind="TOOL_RESULT",
            next_step="STOP",
            text="ok",
        )
        with runtime_binding(None, op):
            apply_engine_io_return(q, run, event, config=config, evaluate=lambda *_, **__: [])

    ends = [
        e
        for e in cap.seen
        if e.attributes.get("activity") == "contract_call"
        and e.attributes.get("boundary") == "end"
        and e.attributes.get("op") == "TOOL_CALL"
    ]
    ends_by_cid = {e.correlation_id: e for e in ends}
    assert set(ends_by_cid) == set(tool_name_by_cid), ends_by_cid
    for cid in tool_name_by_cid:
        assert ends_by_cid[cid].call_id == starts_by_cid[cid].call_id, cid
    # The call-frame stack must be fully unwound: no sibling left stuck.
    assert op._frames.stack == ("exec-001",), op._frames.stack


class _BlockNamed:
    def __init__(self, blocked: set[str]) -> None:
        self.blocked = blocked

    def evaluate_egress(self, intent, *, config):  # noqa: ANN001, ARG002
        if intent.op == "TOOL_CALL" and intent.tool_name in self.blocked:
            return GovDecision.BLOCK, "block-named", f"{intent.tool_name} is denied"
        return GovDecision.ALLOW, "allow", "ok"


def test_parallel_batch_one_denial_keeps_allowed_calls() -> None:
    """A BLOCK on one sibling must not abort the batch or leave a stray LLM wait."""
    q = QProduct()
    run = RunLedger()
    config = KernelConfig(
        parallel_tool_calls=True,
        egress_governance_plugin=_BlockNamed({"tool_1"}),
    )
    tools = [ToolCallSpec(tool_name=f"tool_{i}", tool_arguments={"i": i}) for i in range(3)]

    out = schedule_parallel_tools_egress(q, run, config, tools)

    assert not any(isinstance(sym, RaiseBoundaryError) for sym in out)
    allowed = [sym for sym in out if isinstance(sym, InvokeEngineIo) and sym.op == "TOOL_CALL"]
    assert len(allowed) == 2
    names = {q.pending_tools_by_cid[sym.correlation_id][0] for sym in allowed}
    assert names == {"tool_0", "tool_2"}
    assert pending_for_validate(q) == [sym.correlation_id for sym in allowed]
    denials = [row for row in run.events if row.placeholder]
    assert len(denials) == 1
    assert "tool_1 is denied" in denials[0].text
    assert denials[0].response_kind == "TOOL_RESULT"


def test_parallel_batch_all_denied_feeds_one_llm_call() -> None:
    q = QProduct()
    run = RunLedger()
    config = KernelConfig(
        parallel_tool_calls=True,
        egress_governance_plugin=_BlockNamed({"tool_0", "tool_1"}),
    )
    tools = [ToolCallSpec(tool_name=f"tool_{i}", tool_arguments={"i": i}) for i in range(2)]

    out = schedule_parallel_tools_egress(q, run, config, tools)

    assert not any(isinstance(sym, RaiseBoundaryError) for sym in out)
    assert any(isinstance(sym, InvokeEngineIo) and sym.op == "LLM_CALL" for sym in out)
    assert {row.text for row in run.events if row.placeholder} == {
        "tool_0 is denied",
        "tool_1 is denied",
    }


def test_parallel_batch_hitl_does_not_orphan_earlier_siblings() -> None:
    """HITL on a later call stops the batch; earlier calls leave no dangling wait."""
    from mas.runtime.schema.egress import EmitHitlRequest

    class _HitlNamed:
        def evaluate_egress(self, intent, *, config):  # noqa: ANN001, ARG002
            if intent.op == "TOOL_CALL" and intent.tool_name == "tool_1":
                return GovDecision.HITL, "hitl", "ask"
            return GovDecision.ALLOW, "allow", "ok"

    q = QProduct()
    run = RunLedger()
    config = KernelConfig(parallel_tool_calls=True, egress_governance_plugin=_HitlNamed())
    tools = [ToolCallSpec(tool_name=f"tool_{i}", tool_arguments={}) for i in range(3)]

    out = schedule_parallel_tools_egress(q, run, config, tools)

    assert any(isinstance(sym, EmitHitlRequest) for sym in out)
    hitl_cid = next(sym.request_id for sym in out if isinstance(sym, EmitHitlRequest))
    assert set(pending_for_validate(q)) <= {hitl_cid}
    assert q.pending_tools_by_cid == {}


def _batch_invariants(decisions: tuple[GovDecision, ...]) -> None:
    """Whatever each call's governance decision is, the batch commits as a unit."""

    class _Scripted:
        def evaluate_egress(self, intent, *, config):  # noqa: ANN001, ARG002
            if intent.op != "TOOL_CALL":
                return GovDecision.ALLOW, "allow", "ok"
            return decisions[int(intent.tool_name.split("_")[1])], "scripted", "scripted"

    op = ObservabilityOperator()
    cap = _CapturePlugin()
    op.subscribe(cap)
    op.set_context(agent_id="a", run_id="r")
    op.push_call_frame("exec-001")
    q = QProduct()
    run = RunLedger()
    config = KernelConfig(parallel_tool_calls=True, egress_governance_plugin=_Scripted())
    tools = [ToolCallSpec(tool_name=f"tool_{i}", tool_arguments={}) for i in range(len(decisions))]
    with runtime_binding(None, op):
        out = schedule_parallel_tools_egress(q, run, config, tools)

    emitted = {sym.correlation_id for sym in out if isinstance(sym, InvokeEngineIo) and sym.op == "TOOL_CALL"}
    invoked = {sym.correlation_id for sym in out if isinstance(sym, InvokeEngineIo)}
    from mas.runtime.schema.egress import EmitHitlRequest

    paused = {sym.request_id for sym in out if isinstance(sym, EmitHitlRequest)}
    ctx = f"decisions={[d.value for d in decisions]}"
    # 1. Every outbound wait has an emitted call, and every emitted call a wait.
    assert set(pending_for_validate(q)) - paused == invoked, ctx
    assert set(q.pending_tools_by_cid) == emitted, ctx
    # 2. A batch that stops (HITL / error) emits no tool call at all.
    stopped = any(isinstance(sym, (RaiseBoundaryError, EmitHitlRequest)) for sym in out)
    assert not (stopped and emitted), ctx

    # 3. Every contract_call start without an end belongs to an emitted call.
    def _cids(boundary: str) -> set[int]:
        return {
            e.correlation_id
            for e in cap.seen
            if e.attributes.get("activity") == "contract_call"
            and e.attributes.get("boundary") == boundary
            and e.attributes.get("op") == "TOOL_CALL"
        }

    open_calls = _cids("start") - _cids("end")
    assert open_calls - paused == emitted, ctx


def test_parallel_batch_commits_as_a_unit_for_every_decision_combination() -> None:
    from itertools import product

    for size in (2, 3):
        for combo in product(list(GovDecision), repeat=size):
            _batch_invariants(combo)
