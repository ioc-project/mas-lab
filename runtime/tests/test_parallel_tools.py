#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Regression: schedule_parallel_tools_egress siblings all share one parent call."""

from __future__ import annotations

from dataclasses import dataclass, field

from mas.runtime.boundary.obs.observability_plugin import ObservabilityPlugin
from mas.runtime.boundary.obs.operator import ObservabilityOperator
from mas.runtime.boundary.obs.transition import TransitionEvent
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.ingress_step import apply_engine_io_return
from mas.runtime.kernel.parallel_tools import schedule_parallel_tools_egress
from mas.runtime.kernel.runtime_context import runtime_binding
from mas.runtime.kernel.state import QProduct, RunLedger
from mas.runtime.schema.egress import InvokeEngineIo
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
