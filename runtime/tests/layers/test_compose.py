#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import importlib
from dataclasses import dataclass, field

import pytest
from mas.runtime.layers.compose import LayerLoadError, compose, layer_id, order_steps, split_pipeline_layers
from mas.runtime.layers.defaults import default_pipeline_steps, merge_default_retry
from mas.runtime.reliability.classes import ClassifiedFailure, FailureClass
from mas.runtime.reliability.policy import RetryPolicy
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn


@dataclass
class _CountingInner:
    fail_times: int = 0
    calls: int = 0
    error: Exception | None = field(default=None, repr=False)

    def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.error or ClassifiedFailure(
                "unavailable",
                failure_class=FailureClass.UNAVAILABLE,
                code="UNAVAILABLE",
            )
        return EngineIoReturn(correlation_id=io.correlation_id, response_kind="MODEL_TEXT", next_step="STOP", text="ok")

    async def ainvoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        return self.invoke(io)

    def exchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
        return f"inner:{op}"


def test_retry_outside_breaker_makes_more_inner_calls_than_inside() -> None:
    """Retry wrapping the breaker retries probes; breaker wrapping retry counts one failure."""
    from mas.library.standard.plugins.layers.circuit_breaker import CircuitBreakerLayer
    from mas.library.standard.plugins.layers.retry import RetryLayer

    io = InvokeEngineIo(correlation_id=1, op="LLM_CALL")
    retry_params = {"max_attempts": 4, "backoff_s": 0, "jitter": False, "retry_on": ["unavailable"]}
    breaker_params = {"failure_threshold": 2, "reset_timeout_s": 30, "on": ["unavailable"]}

    inner_outside = _CountingInner(fail_times=10)
    stack_outside = RetryLayer.wrap(
        CircuitBreakerLayer.wrap(inner_outside, breaker_params, call_kinds=("llm",)),
        retry_params,
        call_kinds=("llm",),
    )
    with pytest.raises(ClassifiedFailure):
        stack_outside.invoke(io)

    inner_inside = _CountingInner(fail_times=10)
    stack_inside = CircuitBreakerLayer.wrap(
        RetryLayer.wrap(inner_inside, retry_params, call_kinds=("llm",)),
        breaker_params,
        call_kinds=("llm",),
    )
    with pytest.raises(ClassifiedFailure):
        stack_inside.invoke(io)

    assert inner_outside.calls != inner_inside.calls
    assert inner_outside.calls == 2
    assert inner_inside.calls == 4


def test_per_all_is_common_destination_is_per_leaf() -> None:
    common, dest = split_pipeline_layers(
        [
            {"layer": "llm_cache", "per": "all"},
            {"layer": "retry", "per": "destination"},
        ]
    )
    assert [layer_id(step) for step in common] == ["llm_cache"]
    assert [layer_id(step) for step in dest] == ["retry"]


def test_order_steps_higher_priority_is_outer() -> None:
    ordered = order_steps(
        [
            {"layer": "circuit_breaker", "priority": 60},
            {"layer": "retry", "priority": 80},
        ]
    )
    assert [layer_id(step) for step in ordered] == ["retry", "circuit_breaker"]


def test_documented_default_retry_matches_policy_and_fails_if_removed() -> None:
    steps = default_pipeline_steps()
    assert steps, "runtime/layers/defaults.yaml pipeline is missing"
    llm = next(step for step in steps if "llm" in (step.get("for") or []))
    tools = next(step for step in steps if "tool" in (step.get("for") or []))
    assert llm["params"]["max_attempts"] == RetryPolicy.llm_default().max_attempts == 4
    assert tools["params"]["max_attempts"] == RetryPolicy.tools_default().max_attempts == 2
    assert llm["params"]["retry_on"] == ["transient", "unavailable"]
    assert tools["params"]["retry_on"] == ["transient", "unavailable"]
    assert tools["params"]["require_idempotent"] is True


def test_merge_default_retry_does_not_duplicate() -> None:
    first = merge_default_retry([], "llm")
    second = merge_default_retry(first, "llm")
    assert sum(1 for step in second if layer_id(step) == "retry") == 1


def test_compose_unknown_layer_is_an_error() -> None:
    with pytest.raises(LayerLoadError, match="unknown layer 'retri'"):
        compose(_CountingInner(), [{"layer": "retri"}], kind="llm")


def test_layer_that_does_not_load_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Broken:
        def load_class(self) -> type:
            raise ImportError("no module named gone")

    module = importlib.import_module("mas.runtime.layers.compose")  # the package re-exports a function of this name
    monkeypatch.setattr(module, "_registered_layer", lambda name: (_Broken(), "layer"))
    with pytest.raises(LayerLoadError, match="does not load: no module named gone"):
        compose(_CountingInner(), [{"layer": "retry"}], kind="llm")


def _retry_params(steps: list[dict]) -> dict:
    return next(step for step in steps if layer_id(step) == "retry")["params"]


def test_resolve_call_pipeline_orders_every_source_of_retry() -> None:
    from mas.runtime.layers.control import resolve_call_pipeline

    spec = {"control": {"retry": {"llm": {"max_attempts": 9}}}}
    # defaults < retry_overlay < spec.control.retry < a retry layer's own params
    assert _retry_params(resolve_call_pipeline(None, [], kind="llm"))["max_attempts"] == 4
    assert (
        _retry_params(resolve_call_pipeline(None, [], kind="llm", retry_overlay={"max_attempts": 7}))["max_attempts"]
        == 7
    )
    assert (
        _retry_params(resolve_call_pipeline(spec, [], kind="llm", retry_overlay={"max_attempts": 7}))["max_attempts"]
        == 9
    )
    named = [{"layer": "retry", "for": ["llm"], "params": {"max_attempts": 2}}]
    assert (
        _retry_params(resolve_call_pipeline(spec, named, kind="llm", retry_overlay={"max_attempts": 7}))["max_attempts"]
        == 2
    )
    assert (
        _retry_params(resolve_call_pipeline(None, [], kind="tool", retry_overlay={"max_attempts": 7}))["max_attempts"]
        == 2
    )


def test_overlay_retry_params_does_not_strip_stamps_on_other_steps() -> None:
    from mas.runtime.layers.control import overlay_retry_params
    from mas.runtime.layers.defaults import FROM_DEFAULTS

    steps = merge_default_retry(
        [{"layer": "circuit_breaker", "for": ["llm"], FROM_DEFAULTS: True}],
        "llm",
    )
    out = overlay_retry_params(steps, {"max_attempts": 9})
    breaker = next(step for step in out if layer_id(step) == "circuit_breaker")
    assert breaker.get(FROM_DEFAULTS) is True
    retry = next(step for step in out if layer_id(step) == "retry")
    assert FROM_DEFAULTS not in retry
    assert retry["params"]["max_attempts"] == 9


def test_every_step_has_an_id_an_experiment_can_address() -> None:
    from mas.runtime.layers.compose import stamp_layer_step, step_id

    assert step_id(stamp_layer_step({"layer": "retry", "for": ["llm"]})) == "retry-llm"
    assert step_id(stamp_layer_step({"layer": "retry", "for": ["tool"]})) == "retry-tool"
    assert step_id(stamp_layer_step({"layer": "retry", "for": ["tool"], "id": "tool-retry-b"})) == "tool-retry-b"
    # an id is written only when someone authored it, so an overlay row without one still meets the step
    assert "id" not in stamp_layer_step({"layer": "retry", "for": ["llm"]})
    assert [step["id"] for step in default_pipeline_steps()] == ["retry-llm", "retry-tool"]


def test_a_disabled_step_is_listed_but_does_not_wrap_and_the_default_does_not_return() -> None:
    inner = _CountingInner(fail_times=1)
    off = [{"id": "retry-llm", "layer": "retry", "for": ["llm"], "enabled": False}]
    steps = merge_default_retry(off, "llm")
    assert [step["id"] for step in steps] == ["retry-llm"]  # no default added behind the disabled one
    with pytest.raises(ClassifiedFailure):  # one failure and nobody retries it
        compose(inner, steps, kind="llm").invoke(InvokeEngineIo(correlation_id=1, op="LLM_CALL"))
    assert inner.calls == 1
    on = _CountingInner(fail_times=1)
    compose(on, merge_default_retry([], "llm"), kind="llm").invoke(InvokeEngineIo(correlation_id=1, op="LLM_CALL"))
    assert on.calls == 2
