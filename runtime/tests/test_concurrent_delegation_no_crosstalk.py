#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Concurrent delegate turns keep working memory and call frames isolated."""

from __future__ import annotations

import asyncio

import pytest
from mas.library.standard.plugins.agentcomm.local import LocalAgentComm
from mas.library.standard.plugins.delegation.llm_delegator import LlmDelegator
from mas.runtime.boundary.context.working_memory_registry import (
    WorkingMemoryRegistry,
    WorkingMemorySnapshot,
)
from mas.runtime.boundary.obs.operator import ObservabilityOperator


@pytest.mark.asyncio
async def test_concurrent_adelegate_does_not_crosstalk_working_memory():
    registry = WorkingMemoryRegistry()
    seen: dict[str, str] = {}

    async def asend(agent_id, task, correlation_id, caller_call_id, context_id):
        registry.put(
            "session",
            agent_id,
            WorkingMemorySnapshot(turn_history=[(agent_id, task)]),
        )
        await asyncio.sleep(0.02)
        snap = registry.get("session", agent_id)
        assert snap is not None
        seen[agent_id] = snap.turn_history[0][1]
        return f"ok-{agent_id}"

    def send(agent_id, task, correlation_id, caller_call_id, context_id):
        raise AssertionError("sync send must not run on the async path")

    send.asend = asend  # type: ignore[attr-defined]
    delegator = LlmDelegator(
        comm=LocalAgentComm(send), allowed_peers=["schedule", "concierge"]
    )
    a, b = await asyncio.gather(
        delegator.adelegate("schedule", "trains", caller_call_id="c1"),
        delegator.adelegate("concierge", "fares", caller_call_id="c2"),
    )
    assert a == "ok-schedule"
    assert b == "ok-concierge"
    assert seen["schedule"] == "trains"
    assert seen["concierge"] == "fares"
    assert registry.get("session", "schedule").turn_history[0][0] == "schedule"
    assert registry.get("session", "concierge").turn_history[0][0] == "concierge"


@pytest.mark.asyncio
async def test_concurrent_tasks_keep_independent_call_frames():
    op = ObservabilityOperator()

    async def push_and_read(call_id: str) -> str:
        op.push_call_frame(call_id)
        await asyncio.sleep(0.02)
        current = op._frames.top
        op.pop_call_frame(call_id)
        return current or ""

    left, right = await asyncio.gather(push_and_read("left"), push_and_read("right"))
    assert left == "left"
    assert right == "right"
    assert op._frames.top is None
