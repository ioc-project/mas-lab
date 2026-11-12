#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Parallel tool dispatch — driver batch integrity."""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock

import pytest
from mas.runtime.boundary.obs.observability_plugin import ObservabilityPlugin
from mas.runtime.boundary.obs.transition import TransitionEvent
from mas.runtime.driver.driver import DriverTrace, KernelDriver
from mas.runtime.kernel.orchestrator import RuntimeKernel
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn


@dataclass
class _CapturePlugin(ObservabilityPlugin):
    seen: list[TransitionEvent] = field(default_factory=list)

    def on_transition(self, event: TransitionEvent) -> None:
        self.seen.append(event)


def test_dispatch_engine_batch_records_correct_tool_per_correlation_id():
    """Regression: q.pending_tool_name/pending_tool_args are single shared
    fields that only ever hold the LAST spec set by
    schedule_parallel_tools_egress's per-tool loop (see parallel_tools.py).
    _dispatch_engine_batch used to read those shared fields directly when
    building each sym's CONTRACT_EXECUTE EnvelopeContext, so every
    tool_call_start/CONTRACT_EXECUTE observability event in a batch of N
    *different* tool calls got mistagged with the LAST tool's name/arguments —
    even though q.pending_tools_by_cid (populated per-spec) already had the
    right answer and was being used correctly for actual engine dispatch.
    """
    kernel = RuntimeKernel()

    class _FakeEngine:
        def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
            return EngineIoReturn(
                correlation_id=io.correlation_id,
                response_kind="TOOL_RESULT",
                next_step="STOP",
                text="ok",
            )

    driver = KernelDriver(kernel=kernel, engine=_FakeEngine())
    cap = _CapturePlugin()
    driver.observability.subscribe(cap)
    driver.observability.set_context(agent_id="moderator", run_id="run-1")
    driver.observability.push_call_frame("exec-001")

    q = kernel.q
    q.pending_tools_by_cid[1] = ("delegate_to_schedule_agent", {"task": "look up trains"})
    q.pending_tools_by_cid[2] = ("delegate_to_concierge_agent", {"task": "check fares"})
    # Simulates the leftover state after schedule_parallel_tools_egress's loop
    # has run: these single fields hold whichever spec was set LAST.
    q.pending_tool_name = "delegate_to_concierge_agent"
    q.pending_tool_args = {"task": "check fares"}

    ios = [
        InvokeEngineIo(correlation_id=1, op="TOOL_CALL"),
        InvokeEngineIo(correlation_id=2, op="TOOL_CALL"),
    ]
    driver._dispatch_engine_batch(ios, DriverTrace())

    execs = {e.correlation_id: e for e in cap.seen if e.attributes.get("activity") == "contract_execute"}
    assert set(execs) == {1, 2}
    assert execs[1].attributes.get("tool_name") == "delegate_to_schedule_agent"
    assert execs[1].attributes.get("tool_arguments") == {"task": "look up trains"}
    assert execs[2].attributes.get("tool_name") == "delegate_to_concierge_agent"
    assert execs[2].attributes.get("tool_arguments") == {"task": "check fares"}

    waits = [e for e in cap.seen if e.attributes.get("activity") == "wait_state"]
    assert waits
    assert any(e.phase == "start" and e.attributes.get("wait_role") == "WAIT" for e in waits)
    assert any(e.phase == "end" and e.attributes.get("wait_role") == "RESUME" for e in waits)
    assert {e.attributes.get("wait_link_id") for e in waits if e.correlation_id == 1} == {"delegate-1"}
    assert {e.attributes.get("wait_link_id") for e in waits if e.correlation_id == 2} == {"delegate-2"}

    pgroups = [e for e in cap.seen if e.attributes.get("activity") == "parallel_group"]
    assert pgroups
    for pg in pgroups:
        tools = list(pg.attributes.get("tools") or [])
        assert tools
        assert all(str(t.get("call_id") or "") for t in tools)


def test_parallel_tool_dispatch_strict_zip_raises_on_length_mismatch():
    """Engine pool must return one result per submitted InvokeEngineIo."""
    kernel = RuntimeKernel()
    driver = KernelDriver(kernel=kernel, engine=MagicMock())
    pool = MagicMock()
    pool.submit = MagicMock()
    pool.pending_outbound.return_value = 0
    pool.max_depth = 32
    pool.drain.return_value = [
        EngineIoReturn(
            correlation_id=1,
            response_kind="TOOL_RESULT",
            next_step="STOP",
            text="only one result",
        ),
    ]
    driver.engine_pool = pool

    ios = [
        InvokeEngineIo(correlation_id=1, op="TOOL_CALL"),
        InvokeEngineIo(correlation_id=2, op="TOOL_CALL"),
    ]

    with pytest.raises(ValueError, match="zip"):
        driver._dispatch_engine_batch(ios, DriverTrace())
