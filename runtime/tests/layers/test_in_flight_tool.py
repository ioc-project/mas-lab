#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The tool a call is dispatching is named per engine; layers key their state on it."""

from __future__ import annotations

from typing import Any

import pytest
from mas.library.standard.plugins.layers.circuit_breaker import CircuitBreakerLayer
from mas.runtime.engine import llm_live
from mas.runtime.engine.llm_live import LiveLlmEngine
from mas.runtime.engine.tool_dispatch import IN_FLIGHT_TOOL, InFlightTool
from mas.runtime.reliability.classes import CircuitOpenError, ClassifiedFailure, FailureClass
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn


class _Provider:
    def chat_completion(self, **_: Any) -> dict[str, Any]:
        return {}


def test_a_child_engine_runs_its_own_tool_not_the_one_its_parent_is_running(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[tuple[str, dict[str, Any]]] = []

    def fake_execute(tool: str, **kwargs: Any) -> str:
        ran.append((tool, kwargs["arguments"]))
        return "ok"

    monkeypatch.setattr(llm_live, "execute_engine_tool", fake_execute)
    child = LiveLlmEngine(manifest=None, use_cache=False, llm_provider=_Provider())
    child._pending_tools_by_cid[9] = ("child_tool", {"x": 1})
    # the parent engine is mid-tool (delegating to this child) in the same context
    parent = IN_FLIGHT_TOOL.set(InFlightTool(owner=id(object()), name="parent_tool", arguments={"p": 1}))
    try:
        child.invoke(InvokeEngineIo(correlation_id=9, op="TOOL_CALL"))
        assert IN_FLIGHT_TOOL.get() is not None and IN_FLIGHT_TOOL.get().name == "parent_tool"  # restored after
    finally:
        IN_FLIGHT_TOOL.reset(parent)
    assert ran == [("child_tool", {"x": 1})]
    assert child._pending_tools_by_cid == {}


class _FailingTool:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        self.calls += 1
        raise ClassifiedFailure("down", failure_class=FailureClass.UNAVAILABLE, code="TOOL_UNAVAILABLE")


def _tool_call() -> InvokeEngineIo:
    return InvokeEngineIo(correlation_id=1, op="TOOL_CALL")


def test_each_tool_has_its_own_breaker_target() -> None:
    body = _FailingTool()
    layer = CircuitBreakerLayer.wrap(body, {"failure_threshold": 1}, call_kinds=("tool",))

    def call(name: str) -> None:
        token = IN_FLIGHT_TOOL.set(InFlightTool(owner=1, name=name, arguments={}))
        try:
            layer.invoke(_tool_call())
        finally:
            IN_FLIGHT_TOOL.reset(token)

    with pytest.raises(ClassifiedFailure):
        call("search")
    with pytest.raises(CircuitOpenError):
        call("search")
    with pytest.raises(ClassifiedFailure):  # another tool is not behind search's open circuit
        call("fetch")
    assert body.calls == 2


def test_a_tool_call_with_no_known_tool_is_not_pooled_under_one_breaker() -> None:
    body = _FailingTool()
    layer = CircuitBreakerLayer.wrap(body, {"failure_threshold": 1}, call_kinds=("tool",))
    for _ in range(3):
        with pytest.raises(ClassifiedFailure):
            layer.invoke(_tool_call())
    assert body.calls == 3
