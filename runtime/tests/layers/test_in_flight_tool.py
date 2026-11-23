#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The tool a call is dispatching is named per engine; layers key their state on it."""

from __future__ import annotations

from typing import Any

import pytest
from mas.library.standard.plugins.layers.circuit_breaker import CircuitBreakerLayer
from mas.runtime.engine.llm_live import LiveLlmEngine
from mas.runtime.engine.tool_dispatch import IN_FLIGHT_TOOL, InFlightTool
from mas.runtime.reliability.classes import CircuitOpenError, ClassifiedFailure, FailureClass
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn


class _Provider:
    async def achat_completion(self, **_: Any) -> dict[str, Any]:
        return {}


async def test_a_child_engine_runs_its_own_tool_not_the_one_its_parent_is_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran: list[tuple[str, dict[str, Any]]] = []

    async def fake_execute(tool: str, **kwargs: Any) -> str:
        ran.append((tool, kwargs["arguments"]))
        return "ok"

    monkeypatch.setattr("mas.runtime.engine.tool_dispatch.aexecute_engine_tool", fake_execute)
    child = LiveLlmEngine(manifest=None, use_cache=False, llm_provider=_Provider())
    child._pending_tools_by_cid[9] = ("child_tool", {"x": 1})
    # the parent engine is mid-tool (delegating to this child) in the same context
    parent = IN_FLIGHT_TOOL.set(InFlightTool(owner=id(object()), name="parent_tool", arguments={"p": 1}))
    try:
        await child.ainvoke(InvokeEngineIo(correlation_id=9, op="TOOL_CALL"))
        assert IN_FLIGHT_TOOL.get() is not None and IN_FLIGHT_TOOL.get().name == "parent_tool"  # restored after
    finally:
        IN_FLIGHT_TOOL.reset(parent)
    assert ran == [("child_tool", {"x": 1})]
    assert child._pending_tools_by_cid == {}


async def test_the_tool_in_flight_carries_the_kernels_correlation_id_of_its_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A layer below the engine names the call by the id its governance decision carries."""
    seen: list[InFlightTool | None] = []

    async def fake_execute(tool: str, **kwargs: Any) -> str:
        seen.append(IN_FLIGHT_TOOL.get())
        return "ok"

    monkeypatch.setattr("mas.runtime.engine.tool_dispatch.aexecute_engine_tool", fake_execute)
    engine = LiveLlmEngine(manifest=None, use_cache=False, llm_provider=_Provider())
    engine._pending_tools_by_cid[17] = ("lookup", {})
    await engine.ainvoke(InvokeEngineIo(correlation_id=17, op="TOOL_CALL"))
    assert [(t.name, t.call) for t in seen if t is not None] == [("lookup", 17)]


class _FailingTool:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        self.calls += 1
        raise ClassifiedFailure("down", failure_class=FailureClass.UNAVAILABLE, code="TOOL_UNAVAILABLE")


def _tool_call() -> InvokeEngineIo:
    return InvokeEngineIo(correlation_id=1, op="TOOL_CALL")


async def test_each_tool_has_its_own_breaker_target() -> None:
    body = _FailingTool()
    layer = CircuitBreakerLayer.wrap(body, {"failure_threshold": 1}, call_kinds=("tool",))

    async def call(name: str) -> None:
        token = IN_FLIGHT_TOOL.set(InFlightTool(owner=1, name=name, arguments={}))
        try:
            await layer.ainvoke(_tool_call())
        finally:
            IN_FLIGHT_TOOL.reset(token)

    with pytest.raises(ClassifiedFailure):
        await call("search")
    with pytest.raises(CircuitOpenError):
        await call("search")
    with pytest.raises(ClassifiedFailure):  # another tool is not behind search's open circuit
        await call("fetch")
    assert body.calls == 2


async def test_a_tool_call_with_no_known_tool_is_not_pooled_under_one_breaker() -> None:
    body = _FailingTool()
    layer = CircuitBreakerLayer.wrap(body, {"failure_threshold": 1}, call_kinds=("tool",))
    for _ in range(3):
        with pytest.raises(ClassifiedFailure):
            await layer.ainvoke(_tool_call())
    assert body.calls == 3
