#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest
from mas.runtime.deprecation import reset_deprecation_warnings
from mas.runtime.engine.llm_live import LiveLlmEngine
from mas.runtime.layers.compose import apply_layer, wrap_call_stack
from mas.runtime.layers.lifecycle import Lifecycle
from mas.runtime.reliability.classes import CircuitOpenError, ClassifiedFailure, FailureClass
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn

from .conformance import assert_layer_conformance


@dataclass
class _Inner(Lifecycle):
    calls: int = 0
    started: int = 0
    stopped: int = 0

    def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        self.calls += 1
        return EngineIoReturn(
            correlation_id=io.correlation_id,
            response_kind="MODEL_TEXT",
            next_step="STOP",
            text="ok",
        )

    async def ainvoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        return self.invoke(io)

    def exchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
        return "inner"

    def capabilities(self) -> dict[str, bool]:
        return {"inner": True}

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1


def test_retry_layer_conformance() -> None:
    from mas.library.standard.plugins.infra.llm_cache import LlmCacheMiddleware
    from mas.library.standard.plugins.layers.circuit_breaker import CircuitBreakerLayer
    from mas.library.standard.plugins.layers.retry import RetryLayer

    io = InvokeEngineIo(correlation_id=1, op="LLM_CALL")
    assert_layer_conformance(RetryLayer, io)
    assert_layer_conformance(CircuitBreakerLayer, io)
    assert_layer_conformance(lambda: LlmCacheMiddleware(inner=None, allow_read=False, allow_write=False), io)


def _retry_on_provider(provider: object):
    from mas.library.standard.plugins.layers.retry import RetryLayer

    current: object | None = getattr(provider, "_stack", provider)
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, RetryLayer):
            return current.policy
        try:
            current = current.inner
        except AttributeError:
            current = None
    raise AssertionError("retry layer missing from wrapped provider")


def _chain_names(root: object) -> list[str]:
    names: list[str] = []
    current: object | None = root
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        names.append(type(current).__name__)
        nxt_stack = None
        nxt_inner = None
        try:
            nxt_stack = current._stack
        except AttributeError:
            pass
        try:
            nxt_inner = current.inner
        except AttributeError:
            pass
        current = nxt_stack if nxt_stack is not None else nxt_inner
    return names


def test_default_llm_and_tool_retry_with_no_manifest() -> None:
    engine = LiveLlmEngine(use_cache=False)
    assert engine._tool_stack is not None
    assert "RetryLayer" in _chain_names(engine._tool_stack)
    assert "RetryLayer" in _chain_names(engine.llm_provider)


def test_breaker_opens_and_half_opens() -> None:
    from mas.library.standard.plugins.layers.circuit_breaker import CircuitBreakerLayer

    class Boom(_Inner):
        def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
            self.calls += 1
            raise ClassifiedFailure("down", failure_class=FailureClass.UNAVAILABLE, code="UNAVAILABLE")

    inner = Boom()
    layer = CircuitBreakerLayer.wrap(
        inner,
        {"failure_threshold": 1, "reset_timeout_s": 30, "on": ["unavailable"]},
        call_kinds=("llm",),
    )
    io = InvokeEngineIo(correlation_id=1, op="LLM_CALL")
    with pytest.raises(ClassifiedFailure):
        layer.invoke(io)
    with pytest.raises(CircuitOpenError):
        layer.invoke(io)
    now = {"t": 100.0}
    inner2 = Boom()
    layer2 = CircuitBreakerLayer(
        inner=inner2,
        clock=lambda: now["t"],
        failure_threshold=1,
        reset_timeout_s=30,
        on=["unavailable"],
    )
    with pytest.raises(ClassifiedFailure):
        layer2.invoke(io)
    with pytest.raises(CircuitOpenError):
        layer2.invoke(io)
    now["t"] = 131.0
    with pytest.raises(ClassifiedFailure):
        layer2.invoke(io)
    assert inner2.calls == 2


def test_cache_hit_skips_inner(tmp_path) -> None:
    from mas.library.standard.plugins.infra.fault_inject import FaultInjectMiddleware
    from mas.library.standard.plugins.infra.llm_cache import LlmCacheMiddleware

    inner = _Inner()
    probe = FaultInjectMiddleware.wrap(inner, {"rate": 0.0, "seed": 1}, call_kinds=("llm",))
    cache = LlmCacheMiddleware.wrap(
        probe,
        {"cache_path": str(tmp_path / "c.json"), "allow_read": True, "allow_write": True},
        call_kinds=("llm",),
    )
    io = InvokeEngineIo(correlation_id=1, op="LLM_CALL")
    cache.invoke(io)
    cache.invoke(io)
    assert inner.calls == 1


def test_deprecated_middleware_key_and_type_warn_once() -> None:
    from mas.runtime.engine.call_kinds import reset_alias_warnings

    reset_deprecation_warnings()
    reset_alias_warnings()
    inner = _Inner()
    step = {"middleware": "retry", "params": {"max_attempts": 1, "backoff_s": 0, "jitter": False}}
    with pytest.warns(DeprecationWarning, match="read-only alias of layer"):
        wrap_call_stack(inner, [step])
    wrap_call_stack(inner, [step])


def test_deprecated_infra_middleware_type_still_loads() -> None:
    inner = _Inner()
    wrapped = apply_layer(inner, {"middleware": "llm_cache", "params": {"allow_read": False, "allow_write": False}})
    assert type(wrapped).__name__ == "LlmCacheMiddleware"


def test_spec_control_retry_forwards(caplog: pytest.LogCaptureFixture) -> None:
    from mas.runtime.layers.compose import layer_id
    from mas.runtime.layers.control import pipeline_from_agent_spec

    caplog.set_level(logging.WARNING)
    steps = pipeline_from_agent_spec(
        {"control": {"retry": {"llm": {"max_attempts": 7}}}},
        [],
        kind="llm",
    )
    retry = next(step for step in steps if layer_id(step) == "retry")
    assert retry["params"]["max_attempts"] == 7
    assert "spec.control.retry is deprecated" in caplog.text
    pipeline_from_agent_spec({"control": {"retry": {"llm": {"max_attempts": 7}}}}, [], kind="llm")
    assert caplog.text.count("spec.control.retry is deprecated") == 1


def test_spec_control_circuit_breaker_forwards(caplog: pytest.LogCaptureFixture) -> None:
    from mas.runtime.layers.compose import layer_id
    from mas.runtime.layers.control import pipeline_from_agent_spec

    caplog.set_level(logging.WARNING)
    steps = pipeline_from_agent_spec(
        {"control": {"circuit_breaker": {"failure_threshold": 3, "reset_timeout_s": 10, "on": ["unavailable"]}}},
        [],
        kind="llm",
    )
    breaker = next(step for step in steps if layer_id(step) == "circuit_breaker")
    assert breaker["params"]["failure_threshold"] == 3
    assert "spec.control.circuit_breaker is deprecated" in caplog.text
    pipeline_from_agent_spec(
        {"control": {"circuit_breaker": {"failure_threshold": 3}}},
        [],
        kind="llm",
    )
    assert caplog.text.count("spec.control.circuit_breaker is deprecated") == 1


def test_middleware_key_wraps_the_same_class_as_layer() -> None:
    inner = _Inner()
    with_layer = apply_layer(inner, {"layer": "llm_cache", "params": {"allow_read": False, "allow_write": False}})
    with_old = apply_layer(inner, {"middleware": "llm_cache", "params": {"allow_read": False, "allow_write": False}})
    assert type(with_layer) is type(with_old)


def test_explicit_pipeline_retry_is_not_clobbered_by_overlay() -> None:
    from mas.runtime.registry.llm_provider_registry import llm_providers_from_manifest

    provider = llm_providers_from_manifest(
        None,
        llm_proxy={
            "api_base": "https://x.example/v1",
            "api_key_env": "X_KEY",
            "pipeline": [
                {
                    "layer": "retry",
                    "for": ["llm"],
                    "per": "destination",
                    "params": {"max_attempts": 8, "backoff_s": 0, "jitter": False, "retry_on": ["unavailable"]},
                }
            ],
        },
        retry_overlay={
            "max_attempts": 4,
            "backoff_s": 0.5,
            "backoff_multiplier": 2.0,
            "jitter": True,
            "retry_on": ["transient", "unavailable"],
            "require_idempotent": False,
        },
    )
    policy = _retry_on_provider(provider)
    assert policy.max_attempts == 8
    assert policy.backoff_s == 0
    assert policy.jitter is False


def test_explicit_pipeline_retry_wins_over_overlay() -> None:
    from mas.runtime.registry.llm_provider_registry import llm_providers_from_manifest

    provider = llm_providers_from_manifest(
        None,
        llm_proxy={
            "api_base": "https://x.example/v1",
            "api_key_env": "X_KEY",
            "pipeline": [
                {
                    "layer": "retry",
                    "for": ["llm"],
                    "per": "destination",
                    "params": {"max_attempts": 8, "backoff_s": 0, "jitter": False},
                }
            ],
        },
        retry_overlay={"max_attempts": 6, "backoff_s": 0.05, "jitter": False},
    )
    policy = _retry_on_provider(provider)
    assert policy.max_attempts == 8
    assert policy.backoff_s == 0


def test_default_retry_takes_overlay_when_pipeline_omits_retry() -> None:
    from mas.runtime.registry.llm_provider_registry import llm_providers_from_manifest

    provider = llm_providers_from_manifest(
        None,
        llm_proxy={"api_base": "https://x.example/v1", "api_key_env": "X_KEY"},
        retry_overlay={"max_attempts": 6, "backoff_s": 0.05, "jitter": False},
    )
    policy = _retry_on_provider(provider)
    assert policy.max_attempts == 6
    assert policy.backoff_s == 0.05
    assert policy.jitter is False


def test_yaml_from_defaults_stamp_cannot_open_explicit_retry() -> None:
    from mas.runtime.registry.llm_provider_registry import llm_providers_from_manifest

    provider = llm_providers_from_manifest(
        None,
        llm_proxy={
            "api_base": "https://x.example/v1",
            "api_key_env": "X_KEY",
            "pipeline": [
                {
                    "layer": "retry",
                    "for": ["llm"],
                    "per": "destination",
                    "_from_defaults": True,
                    "params": {"max_attempts": 8, "backoff_s": 0, "jitter": False},
                }
            ],
        },
        retry_overlay={"max_attempts": 6, "backoff_s": 0.05, "jitter": False},
    )
    policy = _retry_on_provider(provider)
    assert policy.max_attempts == 8
    assert policy.backoff_s == 0


def test_spec_control_retry_wins_over_overlay() -> None:
    from mas.runtime.registry.llm_provider_registry import llm_providers_from_manifest

    provider = llm_providers_from_manifest(
        {"spec": {"control": {"retry": {"llm": {"max_attempts": 7}}}}},
        llm_proxy={"api_base": "https://x.example/v1", "api_key_env": "X_KEY"},
        retry_overlay={"max_attempts": 6, "jitter": False},
    )
    assert _retry_on_provider(provider).max_attempts == 7


def test_wrap_call_stack_starts_inner_once() -> None:
    inner = _Inner()
    stack = wrap_call_stack(
        inner,
        [{"layer": "retry", "params": {"max_attempts": 1, "backoff_s": 0, "jitter": False}}],
    )
    assert inner.started == 1
    stack.stop()
    assert inner.stopped == 1


def test_engine_stop_reaches_tool_stack() -> None:
    engine = LiveLlmEngine(use_cache=False)
    inner = _Inner()
    engine._tool_stack = wrap_call_stack(
        inner,
        [{"layer": "retry", "params": {"max_attempts": 1, "backoff_s": 0, "jitter": False}}],
    )
    assert inner.started == 1
    engine.stop()
    assert inner.stopped == 1


def test_deprecated_plugin_types_still_resolve(caplog: pytest.LogCaptureFixture) -> None:
    from mas.runtime.registry import get_registry

    caplog.set_level(logging.WARNING)
    registry = get_registry()
    assert registry.resolve_by_type("infra_middleware", "llm_cache") is not None
    assert registry.resolve_by_type("stack_layer", "delay") is not None
    assert registry.resolve_by_type("circuit_breaker", "threshold") is not None
    registry.resolve_by_type("infra_middleware", "llm_cache")
    assert "deprecated" in caplog.text.lower()


def test_fault_inject_seeded_draws_are_identical() -> None:
    from mas.library.standard.plugins.infra.fault_inject import FaultInjectMiddleware

    a = FaultInjectMiddleware.wrap(_Inner(), {"rate": 0.5, "seed": 11}, call_kinds=("llm",))
    b = FaultInjectMiddleware.wrap(_Inner(), {"rate": 0.5, "seed": 11}, call_kinds=("llm",))
    assert [a._draw() for _ in range(16)] == [b._draw() for _ in range(16)]
