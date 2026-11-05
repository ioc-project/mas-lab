#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Parallel tool-call batches actually overlap on the async path."""

from __future__ import annotations

import asyncio
import time

import pytest
from mas.runtime.driver.driver import DriverTrace, KernelDriver
from mas.runtime.kernel.orchestrator import RuntimeKernel
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn


class _DelayedEngine:
    def __init__(self, delay: float = 0.05) -> None:
        self.delay = delay
        self.in_flight = 0
        self.max_in_flight = 0
        self.order: list[int] = []

    def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        time.sleep(self.delay)
        self.order.append(io.correlation_id)
        return EngineIoReturn(
            correlation_id=io.correlation_id,
            response_kind="TOOL_RESULT",
            next_step="STOP",
            text=f"tool-{io.correlation_id}",
        )

    async def ainvoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
            self.order.append(io.correlation_id)
            return EngineIoReturn(
                correlation_id=io.correlation_id,
                response_kind="TOOL_RESULT",
                next_step="STOP",
                text=f"tool-{io.correlation_id}",
            )
        finally:
            self.in_flight -= 1


def _batch() -> list[InvokeEngineIo]:
    return [
        InvokeEngineIo(correlation_id=1, op="TOOL_CALL"),
        InvokeEngineIo(correlation_id=2, op="TOOL_CALL"),
    ]


@pytest.mark.asyncio
async def test_adispatch_engine_batch_runs_tool_calls_concurrently():
    kernel = RuntimeKernel()
    engine = _DelayedEngine()
    driver = KernelDriver(kernel=kernel, engine=engine)
    q = kernel.q
    q.pending_tools_by_cid[1] = ("alpha", {"n": 1})
    q.pending_tools_by_cid[2] = ("beta", {"n": 2})
    started = time.monotonic()
    results = await driver._adispatch_engine_batch(_batch(), DriverTrace())
    elapsed = time.monotonic() - started
    assert [ret.text for ret in results] == ["tool-1", "tool-2"]
    assert engine.max_in_flight == 2
    # Two 50ms calls overlapping should finish well under the sequential 100ms.
    assert elapsed < 0.15


def test_sync_dispatch_engine_batch_stays_sequential():
    kernel = RuntimeKernel()
    engine = _DelayedEngine(delay=0.02)
    driver = KernelDriver(kernel=kernel, engine=engine)
    q = kernel.q
    q.pending_tools_by_cid[1] = ("alpha", {"n": 1})
    q.pending_tools_by_cid[2] = ("beta", {"n": 2})
    driver._dispatch_engine_batch(_batch(), DriverTrace())
    assert engine.max_in_flight == 0
    assert engine.order == [1, 2]
