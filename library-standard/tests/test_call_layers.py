# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Call layers shipped by library-standard: the breaker around calls, retry, impair, and the lifecycle base."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest
from mas.library.standard.lib.stack import Envelope
from mas.library.standard.lib.stack.call import LayerLifecycle
from mas.library.standard.lib.stack.errors import StackError, StackSendError
from mas.library.standard.lib.stack.handle import normalize_layers
from mas.library.standard.plugins.layers.circuit_breaker import BreakerPolicy, CircuitBreakerLayer
from mas.library.standard.plugins.layers.retry import RetryLayer
from mas.library.standard.plugins.stack.impair import (
    CorruptLayer,
    DelayLayer,
    DropLayer,
    FailLayer,
    ReplayLayer,
)
from mas.library.standard.testing.layer_conformance import RecordingLower
from mas.runtime.engine.tool_dispatch import IN_FLIGHT_TOOL, InFlightTool
from mas.runtime.layers.lifecycle import Lifecycle
from mas.runtime.layers.probe import ToolProber
from mas.runtime.layers.state import MemorySpecPort
from mas.runtime.reliability.classes import CircuitOpenError, ClassifiedFailure, FailureClass
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn


def _llm() -> InvokeEngineIo:
    return InvokeEngineIo(correlation_id=1, op="LLM_CALL")


def _tool() -> InvokeEngineIo:
    return InvokeEngineIo(correlation_id=1, op="TOOL_CALL")


def _ok(io: InvokeEngineIo) -> EngineIoReturn:
    return EngineIoReturn(correlation_id=io.correlation_id, response_kind="MODEL_TEXT", next_step="STOP", text="ok")


def _down(message: str = "down") -> ClassifiedFailure:
    return ClassifiedFailure(message, failure_class=FailureClass.UNAVAILABLE)


class _Body(Lifecycle, ToolProber):
    """The bottom of a call chain: answers, can be made to fail, and can run a tool outside the turn."""

    owns_call_identity = True
    api_base = "http://llm.test"
    model = "m"
    ctx = None

    def __init__(self, *, fail: bool = False, probe_fails: bool = False) -> None:
        self.fail = fail
        self.probe_fails = probe_fails
        self.calls = 0
        self.probed: list[tuple[str, dict[str, Any]]] = []
        self.ticks = 0
        self.log: list[str] = []

    def bind(self, inner: object) -> None:
        return None

    def start(self) -> None:
        self.log.append("start")

    def stop(self) -> None:
        self.log.append("stop")

    def tick(self) -> None:
        self.ticks += 1

    def capabilities(self) -> dict[str, bool]:
        return {"body": True}

    def exchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
        return ""

    def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        self.calls += 1
        if self.fail:
            raise _down()
        return _ok(io)

    async def ainvoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        return self.invoke(io)

    def probe_tool(self, name: str, arguments: Mapping[str, Any]) -> None:
        self.probed.append((name, dict(arguments)))
        if self.probe_fails:
            raise _down("still down")


class _Previewing:
    def __init__(self, text: str) -> None:
        self.text = text

    def exchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
        return self.text


class _Port(MemorySpecPort):
    """A port over a running spec that lists a probe-able tool, and records what is taken away and put back."""

    def __init__(self, probe: dict[str, Any] | None = None) -> None:
        super().__init__()
        self.probe = probe
        self.disabled: list[str] = []
        self.enabled: list[str] = []

    def tool_probe(self, name: str) -> dict[str, Any] | None:
        return self.probe

    def disable_tool(self, name: str, *, by: str, reason: str) -> bool:
        self.disabled.append(name)
        return True

    def enable_tool(self, name: str, *, by: str, reason: str) -> bool:
        self.enabled.append(name)
        return True


# ── circuit breaker ──────────────────────────────────────────────────────────


def test_breaker_lets_calls_through_then_fails_fast_and_async_agrees() -> None:
    body = _Body(fail=True)
    layer = CircuitBreakerLayer.wrap(body, {"failure_threshold": 2, "reset_timeout_s": 30}, call_kinds=("llm",))
    for _ in range(2):
        with pytest.raises(ClassifiedFailure):
            layer.invoke(_llm())
    with pytest.raises(CircuitOpenError):
        layer.invoke(_llm())
    with pytest.raises(CircuitOpenError):
        asyncio.run(layer.ainvoke(_llm()))
    assert body.calls == 2
    assert layer.enabled and layer.failure_threshold == 2 and layer.reset_timeout_s == 30
    assert layer.on == (FailureClass.UNAVAILABLE,)


def test_breaker_async_failures_and_successes_are_recorded() -> None:
    layer = CircuitBreakerLayer(inner=_Body(fail=True), failure_threshold=1, reset_timeout_s=30)
    with pytest.raises(ClassifiedFailure):
        asyncio.run(layer.ainvoke(_llm()))
    assert layer.is_open("llm:http://llm.test")
    healthy = CircuitBreakerLayer(inner=_Body(), failure_threshold=1)
    assert asyncio.run(healthy.ainvoke(_llm())).text == "ok"


def test_breaker_passes_other_kinds_and_nameless_tools_through() -> None:
    body = _Body(fail=True)
    layer = CircuitBreakerLayer(inner=body, failure_threshold=1, reset_timeout_s=30)
    layer.call_kinds = ("llm",)
    with pytest.raises(ClassifiedFailure):
        layer.invoke(_tool())  # not a kind this layer wraps
    with pytest.raises(ClassifiedFailure):
        asyncio.run(layer.ainvoke(_tool()))
    both = CircuitBreakerLayer(inner=body, failure_threshold=1, reset_timeout_s=30)
    for _ in range(2):  # no tool announced: no target, so no breaker opens
        with pytest.raises(ClassifiedFailure):
            both.invoke(_tool())
    with pytest.raises(ClassifiedFailure):
        asyncio.run(both.ainvoke(_tool()))


def test_breaker_reports_itself_in_the_exchange_preview() -> None:
    assert CircuitBreakerLayer(inner=_Previewing("below")).exchange_preview("LLM_CALL") == "[circuit_breaker]\nbelow"
    assert CircuitBreakerLayer(inner=_Previewing("")).exchange_preview("LLM_CALL") == "[circuit_breaker]"


@pytest.mark.parametrize(
    "params, message",
    [
        ({"on": 3}, "list of failure-class"),
        ({"on": ["nonsense"]}, "unknown failure class"),
        ({"failure_threshold": 0}, "failure_threshold"),
        ({"reset_timeout_s": -1}, "reset_timeout_s"),
        ({"bogus": 1}, "unknown field"),
    ],
)
def test_breaker_refuses_bad_params(params: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        BreakerPolicy.from_mapping(params)


def test_breaker_accepts_a_single_class_name() -> None:
    assert BreakerPolicy.from_mapping({"on": "transient"}).on == (FailureClass.TRANSIENT,)
    assert BreakerPolicy.from_mapping({"on": True}).on == (FailureClass.UNAVAILABLE,)
    assert BreakerPolicy.from_mapping({"on": []}).on == (FailureClass.UNAVAILABLE,)


def test_only_a_failed_probe_opens_the_circuit_for_a_class_outside_on() -> None:
    policy = BreakerPolicy(failure_threshold=1, reset_timeout_s=10)
    opened = policy.after_failure({}, "t", FailureClass.UNAVAILABLE, 100.0)
    assert policy.after_failure(opened, "t", FailureClass.TRANSIENT, 101.0) == opened
    probing = policy.admit(opened, "t", 111.0)
    assert policy.after_failure(probing, "t", FailureClass.TRANSIENT, 112.0)["opened_at"] == 112.0
    off = BreakerPolicy(enabled=False)
    assert off.admit({"opened_at": 1.0}, "t", 2.0) == {"opened_at": 1.0}
    assert off.after_failure({}, "t", FailureClass.UNAVAILABLE, 1.0) == {}


def test_an_opened_tool_with_a_probe_leaves_the_spec_and_comes_back_when_the_probe_passes() -> None:
    now = {"t": 1000.0}
    body = _Body()
    port = _Port(probe={"q": "ping"})
    layer = CircuitBreakerLayer(inner=body, failure_threshold=1, reset_timeout_s=30, clock=lambda: now["t"])
    layer._port = lambda: port  # type: ignore[method-assign]
    layer.record_failure("tool:lookup", FailureClass.UNAVAILABLE, port)
    assert port.disabled == ["lookup"]
    layer.tick()  # not yet due
    assert body.probed == []
    now["t"] = 1031.0
    layer.tick()
    assert body.probed == [("lookup", {"q": "ping"})]
    assert port.enabled == ["lookup"]
    assert port.read("circuit_breaker", "tool:lookup") == {}
    assert body.ticks >= 2  # the layer ticks the body below it as well


def test_a_probe_that_fails_keeps_the_tool_out_and_waits_another_timeout() -> None:
    now = {"t": 1000.0}
    body = _Body(probe_fails=True)
    port = _Port(probe={})
    layer = CircuitBreakerLayer(inner=body, failure_threshold=1, reset_timeout_s=30, clock=lambda: now["t"])
    layer._port = lambda: port  # type: ignore[method-assign]
    layer.record_failure("tool:lookup", FailureClass.UNAVAILABLE, port)
    now["t"] = 1031.0
    layer.tick()
    assert port.enabled == []
    assert port.read("circuit_breaker", "tool:lookup")["opened_at"] == 1031.0
    now["t"] = 1040.0
    layer.tick()
    assert len(body.probed) == 1  # not due again yet


def test_tick_skips_what_it_cannot_probe() -> None:
    now = {"t": 2000.0}
    port = _Port(probe=None)  # the spec declares no probe for this tool
    body = _Body()
    layer = CircuitBreakerLayer(inner=body, failure_threshold=1, reset_timeout_s=1, clock=lambda: now["t"])
    layer._port = lambda: port  # type: ignore[method-assign]
    layer.record_failure("tool:plain", FailureClass.UNAVAILABLE, port)
    layer.record_failure("llm:x", FailureClass.UNAVAILABLE, port)
    assert port.disabled == []  # no probe, so the tool stays offered
    now["t"] = 2010.0
    layer.tick()
    assert body.probed == []
    no_prober = CircuitBreakerLayer(inner=_Previewing(""), failure_threshold=1, clock=lambda: now["t"])
    no_prober._port = lambda: port  # type: ignore[method-assign]
    no_prober.tick()
    llm_only = CircuitBreakerLayer(inner=body, failure_threshold=1)
    llm_only.call_kinds = ("llm",)
    llm_only.tick()
    CircuitBreakerLayer(inner=body, enabled=False).tick()


def test_a_tool_call_that_keeps_failing_is_recorded_under_its_name() -> None:
    layer = CircuitBreakerLayer(inner=_Body(fail=True), failure_threshold=1, reset_timeout_s=30)
    token = IN_FLIGHT_TOOL.set(InFlightTool(owner=1, name="lookup", arguments={}))
    try:
        with pytest.raises(ClassifiedFailure):
            layer.invoke(_tool())
        with pytest.raises(CircuitOpenError):
            layer.invoke(_tool())
    finally:
        IN_FLIGHT_TOOL.reset(token)
    assert layer.is_open("tool:lookup")


# ── lifecycle base ───────────────────────────────────────────────────────────


def test_layer_lifecycle_binds_starts_and_stops_once_and_merges_capabilities() -> None:
    body = _Body()

    class Mine(LayerLifecycle):
        def own_capabilities(self) -> dict[str, bool]:
            return {"mine": True}

    layer = Mine()
    layer.bind(body)
    layer.start()
    layer.start()
    layer.stop()
    layer.stop()
    layer.tick()
    assert body.log == ["start", "stop"] and body.ticks == 1
    assert layer.capabilities() == {"body": True, "mine": True}
    assert LayerLifecycle().own_capabilities() == {}
    bare = LayerLifecycle()
    bare.inner = object()
    assert bare.capabilities() == {}


# ── retry ────────────────────────────────────────────────────────────────────


def test_retry_layer_wraps_previews_and_passes_other_kinds_through() -> None:
    body = _Body()
    layer = RetryLayer.wrap(body, {"max_attempts": 2, "backoff_s": 0, "jitter": False}, call_kinds=("llm",))
    assert layer.call_kinds == ("llm",)
    assert layer.exchange_preview("LLM_CALL") == "[retry max_attempts=2]"
    assert layer.invoke(_tool()).text == "ok"  # a tool call is not this layer's kind
    assert asyncio.run(layer.ainvoke(_tool())).text == "ok"
    assert layer.invoke(_llm()).text == "ok"
    assert RetryLayer(inner=_Previewing("below")).exchange_preview("LLM_CALL").endswith("below")


# ── impair layers ────────────────────────────────────────────────────────────


def _bound(layer: Any) -> tuple[Any, RecordingLower]:
    lower = RecordingLower()
    layer.bind(lower)
    return layer, lower


def test_impair_layers_act_on_a_send() -> None:
    layer, _ = _bound(DropLayer(probability=1.0))
    with pytest.raises(StackSendError):
        layer.send(Envelope(payload="x"))
    with pytest.raises(StackSendError):
        asyncio.run(layer.asend(Envelope(payload="x")))
    layer, _ = _bound(FailLayer(message="boom"))
    with pytest.raises(StackSendError, match="boom"):
        layer.send(Envelope(payload="x"))
    with pytest.raises(StackSendError, match="boom"):
        asyncio.run(layer.asend(Envelope(payload="x")))
    layer, lower = _bound(CorruptLayer(suffix="!"))
    layer.send(Envelope(payload="a"))
    layer.send(Envelope(payload=b"b"))
    layer.send(Envelope(payload=3))
    assert [e.payload for e in lower.seen] == ["a!", b"b!", 3]
    layer, lower = _bound(ReplayLayer(times=3))
    layer.send(Envelope(payload="x"))
    assert len(lower.seen) == 3
    layer, lower = _bound(DelayLayer(ms=1))
    layer.send(Envelope(payload="x"))
    assert len(lower.seen) == 1


def test_impair_layers_act_on_llm_calls_and_honour_call_kinds() -> None:
    body = _Body()
    drop = DropLayer(probability=1.0)
    drop.bind(body)
    with pytest.raises(StackSendError):
        drop.invoke(_llm())
    with pytest.raises(StackSendError):
        asyncio.run(drop.ainvoke(_llm()))
    drop.call_kinds = ("tool",)
    assert drop.invoke(_llm()).text == "ok"  # an llm call is not a kind this layer wraps
    assert asyncio.run(drop.ainvoke(_llm())).text == "ok"
    delay = DelayLayer(seconds=0)
    delay.bind(body)
    assert delay.invoke(_llm()).text == "ok"
    assert asyncio.run(delay.ainvoke(_llm())).text == "ok"
    replay = ReplayLayer(times=2)
    replay.bind(body)
    replay.call_kinds = ("llm",)
    before = body.calls
    replay.invoke(_llm())
    assert body.calls == before + 2
    assert asyncio.run(replay.ainvoke(_llm())).text == "ok"


def test_impair_layers_refuse_bad_params_and_report_capabilities() -> None:
    for bad in (0, 33, True, "x", 2.5):
        with pytest.raises(ValueError):
            ReplayLayer(times=bad)
    with pytest.raises(ValueError):
        DropLayer(probability=2)
    with pytest.raises(ValueError):
        DelayLayer(seconds=1, ms=1)
    with pytest.raises(ValueError):
        FailLayer(nonsense=1)
    layer = DelayLayer(seconds=0)
    layer.bind(_Body())
    assert layer.capabilities().get("body") is True
    layer.start()
    layer.stop()
    assert DelayLayer.wrap(_Body(), {"seconds": 0}, call_kinds=("llm",)).call_kinds == ("llm",)


def test_impair_delay_defaults() -> None:
    assert DelayLayer()._delay_s() == 0.05
    assert DelayLayer(ms=20)._delay_s() == 0.02


# ── layer entries ────────────────────────────────────────────────────────────


def test_a_layer_entry_keeps_its_priority_and_per() -> None:
    assert normalize_layers([{"layer": "retry", "priority": 5, "per": "all", "params": {"a": 1}}]) == [
        {"type": "retry", "params": {"a": 1}, "priority": 5, "per": "all"}
    ]
    with pytest.raises(StackError):
        normalize_layers([{"type": "x", "bogus": 1}])
