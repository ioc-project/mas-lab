#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A call layer works on the session's running spec only through the session: its state machine orders it."""

from __future__ import annotations

from typing import Any

import pytest
from mas.library.standard.plugins.layers.circuit_breaker import CircuitBreakerLayer
from mas.runtime.boundary.control.contract import ControlDenied
from mas.runtime.engine.tool_dispatch import IN_FLIGHT_TOOL, InFlightTool
from mas.runtime.engine.tools import disabled_tool_names
from mas.runtime.layers.probe import ToolProber
from mas.runtime.reliability.classes import CircuitOpenError, ClassifiedFailure, FailureClass
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn
from mas.runtime.session import SessionStatus, SpecConflict
from test_spec_revision import _manager_with_session


class _Down(ToolProber):
    """The call under the breaker: it owns the session's turn context, fails, and can be probed."""

    def __init__(self, ctx: object) -> None:
        self.ctx = ctx
        self.calls = 0
        self.probes: list[tuple[str, dict[str, Any]]] = []
        self.back = False

    def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        self.calls += 1
        raise ClassifiedFailure("down", failure_class=FailureClass.UNAVAILABLE, code="UNAVAILABLE")

    def probe_tool(self, name: str, arguments: Any) -> None:
        self.probes.append((name, dict(arguments)))
        if not self.back:
            raise ClassifiedFailure("still down", failure_class=FailureClass.UNAVAILABLE, code="TOOL_UNAVAILABLE")


def _breaker(ctx: object, *, clock: Any = None, kinds: tuple[str, ...] = ("llm",)) -> tuple[CircuitBreakerLayer, _Down]:
    body = _Down(ctx)
    extra = {"clock": clock} if clock else {}
    layer = CircuitBreakerLayer(inner=body, failure_threshold=2, reset_timeout_s=30, **extra)
    layer.call_kinds = kinds
    return layer, body


def _llm_call() -> InvokeEngineIo:
    return InvokeEngineIo(correlation_id=1, op="LLM_CALL")


def _tool_call(layer: CircuitBreakerLayer, name: str) -> None:
    token = IN_FLIGHT_TOOL.set(InFlightTool(owner=1, name=name, arguments={}))
    try:
        layer.invoke(InvokeEngineIo(correlation_id=1, op="TOOL_CALL"))
    finally:
        IN_FLIGHT_TOOL.reset(token)


_SPEC_WITH_PROBE = {
    "name": "agent",
    "spec": {"tools": [{"name": "lookup", "enabled": True, "probe": {"arguments": {"q": "ping"}}}, {"name": "plain"}]},
}


def test_session_hands_layers_a_handle_that_can_only_edit_what_a_layer_needs() -> None:
    manager, sid = _manager_with_session()
    handle = manager.get(sid).instance.driver.ctx.layer_control
    assert handle is not None
    assert handle.layer_state(sid, layer="circuit_breaker") == {}
    rev = handle.swap_layer_state(
        sid, layer="circuit_breaker", target="tool:x", expected={}, state={"failures": 1}, reason="t"
    )
    assert rev == 1
    assert handle.layer_state(sid, layer="circuit_breaker") == {"tool:x": {"failures": 1}}
    with pytest.raises(ControlDenied):
        handle.pause(sid, reason="a layer may not pause")
    with pytest.raises(ControlDenied):
        handle.navigate(sid, to="x", reason="nor walk the snapshot tree")


def test_a_stale_swap_is_refused_not_merged() -> None:
    manager, sid = _manager_with_session()
    handle = manager.get(sid).instance.driver.ctx.layer_control
    handle.swap_layer_state(sid, layer="l", target="t", expected={}, state={"n": 1}, reason="first")
    with pytest.raises(SpecConflict):
        handle.swap_layer_state(sid, layer="l", target="t", expected={}, state={"n": 2}, reason="second")
    assert handle.layer_state(sid, layer="l") == {"t": {"n": 1}}
    assert manager.get(sid).spec_revision == 1  # the refused write made no revision


def test_tripping_the_breaker_revises_the_running_spec_and_traces_it() -> None:
    manager, sid = _manager_with_session()
    session = manager.get(sid)
    layer, body = _breaker(session.instance.driver.ctx)
    for _ in range(2):
        with pytest.raises(ClassifiedFailure):
            layer.invoke(_llm_call())
    spec = manager.spec_log.current_spec(sid)
    [(target, state)] = spec["layer_state"]["circuit_breaker"].items()
    assert target.startswith("llm:") and state["failures"] == 2 and state["opened_at"] > 0
    assert session.spec_revision == 2  # one revision per recorded change
    events = [e for e in manager.control_events if e.method == "swap_layer_state"]
    assert [e.actor for e in events] == ["layers", "layers"] and events[-1].kind == "spec_revised"
    assert "circuit_breaker opened" in events[-1].reason
    with pytest.raises(CircuitOpenError):
        layer.invoke(_llm_call())
    assert body.calls == 2  # the open circuit did not reach the body


def test_a_tripped_breaker_is_restored_with_the_snapshot() -> None:
    manager, sid = _manager_with_session()
    session = manager.get(sid)
    ctx = session.instance.driver.ctx
    origin = session.take_snapshot(label="before")
    layer, _ = _breaker(ctx)
    for _ in range(2):
        with pytest.raises(ClassifiedFailure):
            layer.invoke(_llm_call())
    with pytest.raises(CircuitOpenError):
        layer.invoke(_llm_call())
    session.restore_snapshot(origin)  # what-if: go back to before the failures
    assert "layer_state" not in manager.spec_log.current_spec(sid)
    fresh, body = _breaker(ctx)
    with pytest.raises(ClassifiedFailure):  # closed again: the call reaches the body
        fresh.invoke(_llm_call())
    assert body.calls == 1


def test_a_second_layer_instance_sees_the_state_the_first_recorded() -> None:
    manager, sid = _manager_with_session()
    ctx = manager.get(sid).instance.driver.ctx
    first, _ = _breaker(ctx)
    for _ in range(2):
        with pytest.raises(ClassifiedFailure):
            first.invoke(_llm_call())
    second, body = _breaker(ctx)
    with pytest.raises(CircuitOpenError):
        second.invoke(_llm_call())
    assert body.calls == 0


def test_the_session_refuses_spec_changes_while_it_checkpoints_and_the_call_goes_on() -> None:
    manager, sid = _manager_with_session()
    session = manager.get(sid)
    handle = session.instance.driver.ctx.layer_control
    session.status = SessionStatus.CHECKPOINTING
    with pytest.raises(ControlDenied, match="checkpointing"):
        handle.swap_layer_state(sid, layer="l", target="t", expected={}, state={"n": 1}, reason="r")
    assert any(e.denied and e.method == "spec_delta" for e in manager.control_events)
    layer, body = _breaker(session.instance.driver.ctx)
    with pytest.raises(ClassifiedFailure):  # the breaker could not record, and the failure still reaches the caller
        layer.invoke(_llm_call())
    assert body.calls == 1 and session.spec_revision == 0
    session.status = SessionStatus.ACTIVE


def test_a_down_tool_with_a_probe_leaves_the_spec_and_comes_back_when_the_probe_passes() -> None:
    manager, sid = _manager_with_session(_SPEC_WITH_PROBE)
    session = manager.get(sid)
    now = {"t": 1000.0}
    layer, body = _breaker(session.instance.driver.ctx, clock=lambda: now["t"], kinds=("tool",))
    for _ in range(2):
        with pytest.raises(ClassifiedFailure):
            _tool_call(layer, "lookup")
    spec = manager.spec_log.current_spec(sid)
    assert "lookup" in disabled_tool_names(spec)  # the model no longer sees it
    assert spec["tools"][0]["disabled_by"] == "circuit_breaker"
    layer.tick()  # too early: nothing to probe yet
    assert body.probes == []
    now["t"] = 1031.0
    layer.tick()  # still down: the probe fails, the circuit opens again
    assert body.probes == [("lookup", {"q": "ping"})]
    assert "lookup" in disabled_tool_names(manager.spec_log.current_spec(sid))
    now["t"] = 1040.0
    layer.tick()  # not due again yet
    assert len(body.probes) == 1
    body.back = True
    now["t"] = 1070.0
    layer.tick()  # back: the circuit closes and the tool returns to the spec
    spec = manager.spec_log.current_spec(sid)
    assert "lookup" not in disabled_tool_names(spec) and "disabled_by" not in spec["tools"][0]
    assert "layer_state" not in spec
    reasons = [e.reason for e in manager.control_events if e.method in {"disable_tool", "enable_tool"}]
    assert reasons == ["circuit_breaker opened tool:lookup", "circuit_breaker closed tool:lookup"]


def test_a_tool_without_a_probe_stays_in_the_spec_when_its_circuit_opens() -> None:
    manager, sid = _manager_with_session(_SPEC_WITH_PROBE)
    layer, _ = _breaker(manager.get(sid).instance.driver.ctx, kinds=("tool",))
    for _ in range(2):
        with pytest.raises(ClassifiedFailure):
            _tool_call(layer, "plain")
    assert disabled_tool_names(manager.spec_log.current_spec(sid)) == frozenset()
    with pytest.raises(CircuitOpenError):
        _tool_call(layer, "plain")


def test_the_breaker_does_not_put_back_a_tool_an_operator_disabled() -> None:
    manager, sid = _manager_with_session(_SPEC_WITH_PROBE)
    session = manager.get(sid)
    admin = manager.control()
    admin.disable_tool(sid, name="lookup", reason="governance")  # an operator's own disable
    handle = session.instance.driver.ctx.layer_control
    with pytest.raises(SpecConflict):
        handle.enable_tool(sid, name="lookup", reason="breaker closed", by="circuit_breaker")
    assert "lookup" in disabled_tool_names(manager.spec_log.current_spec(sid))
