#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for bidirectional infra pipeline engine wrapping."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from mas.library.standard.plugins.infra.llm_cache import (
    LlmCacheMiddleware,
    reset_llm_cache_replace_guard,
)
from mas.runtime.engine.infra_pipeline import (
    BidirectionalPipelineEngine,
    PipedLLMProvider,
    apply_middleware,
    split_pipeline_layers,
    wrap_call_stack,
    wrap_llm_provider_pipeline,
    wrap_tool_pipeline,
)
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn


@dataclass
class _EchoEngine:
    preview_text: str = "echo:LLM_CALL"
    calls: int = 0
    model: str = ""
    api_base: str = ""
    llm_proxies: dict | None = None
    llm_proxy: dict | None = None

    def exchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
        return self.preview_text if op == "LLM_CALL" else f"echo:{op}"

    def reset_turn_state(self) -> None:
        return None

    def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        self.calls += 1
        return EngineIoReturn(
            correlation_id=io.correlation_id,
            response_kind="MODEL_TEXT",
            next_step="STOP",
            text=f"live-response-{self.calls}",
        )

    async def ainvoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        return self.invoke(io)


def test_wrap_call_stack_uses_cache(tmp_path):
    cache_path = tmp_path / "cache.json"
    pipeline = [{"middleware": "llm_cache", "params": {"cache_path": str(cache_path)}}]
    inner = _EchoEngine()
    engine = wrap_call_stack(inner, pipeline)

    io = InvokeEngineIo(correlation_id=1, op="LLM_CALL")
    first = engine.invoke(io)
    assert first.text == "live-response-1"
    assert inner.calls == 1

    second = engine.invoke(io)
    assert second.text == "live-response-1"
    assert inner.calls == 1
    assert cache_path.is_file()


def test_write_mode_replace_truncates_once_per_process(tmp_path):

    reset_llm_cache_replace_guard()
    cache_path = tmp_path / "cache.json"
    cache_path.write_text('{"old": {"response_kind": "MODEL_TEXT", "next_step": "STOP", "text": "stale"}}')
    first = LlmCacheMiddleware(
        inner=_EchoEngine(),
        cache_path=cache_path,
        allow_write=True,
        write_mode="replace",
    )
    assert "old" not in first._cache
    assert cache_path.read_text(encoding="utf-8") == "{}"

    cache_path.write_text('{"kept": {"response_kind": "MODEL_TEXT", "next_step": "STOP", "text": "live"}}')
    second = LlmCacheMiddleware(
        inner=_EchoEngine(),
        cache_path=cache_path,
        allow_write=True,
        write_mode="replace",
    )
    assert "kept" in second._cache
    reset_llm_cache_replace_guard()


def test_write_mode_append_keeps_existing_keys(tmp_path):

    reset_llm_cache_replace_guard()
    cache_path = tmp_path / "cache.json"
    cache_path.write_text('{"old": {"response_kind": "MODEL_TEXT", "next_step": "STOP", "text": "stale"}}')
    engine = LlmCacheMiddleware(
        inner=_EchoEngine(),
        cache_path=cache_path,
        allow_write=True,
        write_mode="append",
    )
    assert "old" in engine._cache


def test_bidirectional_engine_backward_reply_passthrough():
    echo = _EchoEngine()
    inner = LlmCacheMiddleware(inner=echo, allow_read=False, allow_write=False)
    facade = BidirectionalPipelineEngine(inner=inner, pipeline_steps=[])
    io = InvokeEngineIo(correlation_id=2, op="LLM_CALL")
    ret = facade.invoke(io)
    assert ret.text == "live-response-1"


def test_llm_cache_raise_on_miss(tmp_path):
    cache_path = tmp_path / "cache.json"
    engine = apply_middleware(
        _EchoEngine(),
        {
            "middleware": "llm_cache",
            "params": {"cache_path": str(cache_path), "raise_on_miss": True},
        },
    )
    io = InvokeEngineIo(correlation_id=1, op="LLM_CALL")
    with pytest.raises(RuntimeError, match="llm_cache miss") as excinfo:
        engine.invoke(io)
    assert "echo:LLM_CALL" in str(excinfo.value)


def test_llm_cache_read_only_skips_write(tmp_path):
    cache_path = tmp_path / "cache.json"
    inner = _EchoEngine()
    engine = apply_middleware(
        inner,
        {
            "middleware": "llm_cache",
            "params": {"cache_path": str(cache_path), "allow_write": False},
        },
    )
    io = InvokeEngineIo(correlation_id=1, op="LLM_CALL")
    engine.invoke(io)
    assert inner.calls == 1
    engine.invoke(io)
    assert inner.calls == 2
    assert not cache_path.exists()


def test_llm_cache_write_only_skips_read(tmp_path):
    cache_path = tmp_path / "cache.json"
    inner = _EchoEngine()
    engine = apply_middleware(
        inner,
        {
            "middleware": "llm_cache",
            "params": {"cache_path": str(cache_path), "allow_read": False, "allow_write": True},
        },
    )
    io = InvokeEngineIo(correlation_id=1, op="LLM_CALL")
    engine.invoke(io)
    engine.invoke(io)
    assert inner.calls == 2


def test_llm_cache_caches_post_tool_preview(tmp_path):
    cache_path = tmp_path / "cache.json"
    inner = _EchoEngine(preview_text="history [tool call_id=call_1] result")
    engine = apply_middleware(
        inner,
        {"middleware": "llm_cache", "params": {"cache_path": str(cache_path)}},
    )
    io = InvokeEngineIo(correlation_id=1, op="LLM_CALL")
    first = engine.invoke(io)
    second = engine.invoke(io)
    assert first.text == "live-response-1"
    assert second.text == "live-response-1"
    assert inner.calls == 1


def test_live_engine_keeps_tool_setters_with_pipeline() -> None:
    """Callers talk to LiveLlmEngine; cache is a step, not a disguise."""
    from mas.runtime.engine.llm_live import LiveLlmEngine

    engine = LiveLlmEngine(manifest=None, use_cache=False, llm_provider=_StubProvider())
    engine.install_infra_pipeline(
        [{"middleware": "llm_cache", "params": {"allow_read": False, "allow_write": False}}],
    )
    engine.set_tool_for_correlation(3, "calc", {"expression": "2+2"})
    engine.set_scheduled_tool("web-search", {"query": "POTUS"})
    assert engine._pending_tools_by_cid[3] == ("calc", {"expression": "2+2"})
    assert engine._pending_tool == "web-search"
    assert type(engine) is LiveLlmEngine
    assert "__getattr__" not in LiveLlmEngine.__dict__


def test_infra_has_cache_step_ignores_scheduler() -> None:
    from mas.runtime.engine.infra_pipeline import infra_has_cache_step

    assert infra_has_cache_step([{"middleware": "llm_scheduler", "params": {}}]) is False
    assert infra_has_cache_step([{"middleware": "llm_cache", "params": {}}]) is True
    assert (
        infra_has_cache_step(
            [],
            {"arm": {"pipeline": [{"middleware": "llm_cache", "params": {}}]}},
        )
        is True
    )


def test_split_pipeline_layers_keeps_cache_common() -> None:
    common, per_leaf = split_pipeline_layers(
        [
            {"middleware": "llm_cache", "params": {"allow_read": True}},
            {"middleware": "fault_inject", "params": {"rate": 0.1}},
            {"middleware": "llm_scheduler", "params": {"max_in_flight": 2}},
        ]
    )
    assert [step["middleware"] for step in common] == ["llm_cache"]
    assert [step["middleware"] for step in per_leaf] == ["fault_inject", "llm_scheduler"]


class _StubProvider:
    kind = "openai"
    provider_id = "openai"
    api_base = "https://arm.example/v1"
    api_key_env = "ARM_KEY"

    def __init__(self) -> None:
        self.calls = 0

    def chat_completion(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        return {"role": "assistant", "content": "ok"}

    async def achat_completion(self, **kwargs):
        return self.chat_completion(**kwargs)


@pytest.mark.parametrize(
    "step",
    [
        {"middleware": "llm_scheduler", "params": {"max_in_flight": 2}},
        {"middleware": "fault_inject", "params": {"rate": 0.0}},
        {"middleware": "llm_cache", "params": {"allow_read": False, "allow_write": False}},
    ],
)
def test_piped_llm_provider_copy_and_deepcopy(step) -> None:
    import copy
    import pickle

    piped = PipedLLMProvider(inner=_StubProvider(), pipeline=[step])
    with pytest.raises(AttributeError):
        _ = piped.not_a_real_attr
    assert "__getattr__" not in type(piped).__dict__
    cloned = copy.copy(piped)
    assert cloned.chat_completion(model="m", messages=[])["content"] == "ok"
    deep = copy.deepcopy(piped)
    assert deep.chat_completion(model="m", messages=[])["content"] == "ok"
    restored = pickle.loads(pickle.dumps(piped))
    assert restored.chat_completion(model="m", messages=[])["content"] == "ok"


def test_wrap_llm_provider_pipeline_instantiates_fault_inject() -> None:
    inner = _StubProvider()
    wrapped = wrap_llm_provider_pipeline(
        inner,
        [{"middleware": "fault_inject", "params": {"rate": 1.0, "message": "injected"}}],
    )
    assert isinstance(wrapped, PipedLLMProvider)
    with pytest.raises(RuntimeError, match="injected"):
        wrapped.chat_completion(model="m", messages=[{"role": "user", "content": "hi"}])
    assert inner.calls == 0


def test_cache_key_includes_model(tmp_path) -> None:
    cache_path = tmp_path / "cache.json"
    pipeline = [{"middleware": "llm_cache", "params": {"cache_path": str(cache_path)}}]
    first = wrap_call_stack(
        _EchoEngine(preview_text="same-prompt", model="m-a", api_base="https://a.example/v1"),
        pipeline,
    )
    io = InvokeEngineIo(correlation_id=1, op="LLM_CALL")
    assert first.invoke(io).text == "live-response-1"
    second_inner = _EchoEngine(preview_text="same-prompt", model="m-b", api_base="https://b.example/v1")
    second = wrap_call_stack(second_inner, pipeline)
    out = second.invoke(io)
    assert out.text == "live-response-1"
    assert second_inner.calls == 1


def test_wrap_llm_provider_pipeline_empty_returns_inner() -> None:
    inner = _StubProvider()
    assert wrap_llm_provider_pipeline(inner, []) is inner


def test_legacy_middleware_cache_hits_with_one_leaf(tmp_path) -> None:
    import json

    from mas.runtime.engine.llm_cache import bind_route_cache_key, legacy_preview_cache_key

    cache_path = tmp_path / "cache.json"
    preview = "same-prompt"
    cache_path.write_text(json.dumps({legacy_preview_cache_key(preview): "old-hit"}), encoding="utf-8")
    inner = _EchoEngine(preview_text=preview, model="m-a", api_base="https://a.example/v1")
    engine = wrap_call_stack(
        inner,
        [{"middleware": "llm_cache", "params": {"cache_path": str(cache_path)}}],
    )
    out = engine.invoke(InvokeEngineIo(correlation_id=1, op="LLM_CALL"))
    assert out.text == "old-hit"
    assert inner.calls == 0
    stored = json.loads(cache_path.read_text(encoding="utf-8"))
    routed = bind_route_cache_key(preview, leaf="", model="m-a", api_base="https://a.example/v1")
    assert routed in stored
    assert stored[routed] == "old-hit" or "old-hit" in stored.values()


def test_legacy_middleware_cache_misses_with_several_leaves(tmp_path) -> None:
    import json

    from mas.runtime.engine.llm_cache import legacy_preview_cache_key

    cache_path = tmp_path / "cache.json"
    preview = "same-prompt"
    cache_path.write_text(json.dumps({legacy_preview_cache_key(preview): "cross-host"}), encoding="utf-8")
    inner = _EchoEngine(
        preview_text=preview,
        model="m-a",
        api_base="https://a.example/v1",
        llm_proxies={
            "arm": {"api_base": "https://a.example/v1", "api_key_env": "A_KEY", "allowed": ["m-a"]},
            "judge": {"api_base": "https://b.example/v1", "api_key_env": "B_KEY", "allowed": ["m-b"]},
        },
    )
    engine = wrap_call_stack(
        inner,
        [{"middleware": "llm_cache", "params": {"cache_path": str(cache_path)}}],
    )
    out = engine.invoke(InvokeEngineIo(correlation_id=1, op="LLM_CALL"))
    assert out.text == "live-response-1"
    assert inner.calls == 1


def test_split_pipeline_honours_explicit_per() -> None:
    common, per_leaf = split_pipeline_layers(
        [
            {"middleware": "custom_limiter", "per": "all"},
            {"middleware": "fault_inject", "params": {"rate": 0.1}},
        ]
    )
    assert [step["middleware"] for step in common] == ["custom_limiter"]
    assert [step["middleware"] for step in per_leaf] == ["fault_inject"]


def test_two_cache_steps_warn(caplog) -> None:
    import logging

    caplog.set_level(logging.WARNING)
    split_pipeline_layers(
        [
            {"middleware": "llm_cache", "params": {}},
            {"middleware": "llm_cache", "params": {"allow_read": False}},
        ]
    )
    assert "llm_cache" in caplog.text


def test_wrap_llm_provider_skips_tool_steps() -> None:
    inner = _StubProvider()
    wrapped = wrap_llm_provider_pipeline(
        inner,
        [{"middleware": "fault_inject", "for": "tool", "params": {"rate": 1.0}}],
    )
    assert wrapped is inner
    assert inner.chat_completion(model="m", messages=[])["content"] == "ok"


def test_wrap_tool_pipeline_skips_llm_steps() -> None:
    body = _EchoEngine()
    wrapped = wrap_tool_pipeline(
        body,
        [{"middleware": "llm_scheduler", "params": {"max_in_flight": 1}}],
    )
    assert wrapped is body


def test_two_leaf_schedulers_are_separate_instances() -> None:
    first = wrap_llm_provider_pipeline(
        _StubProvider(),
        [{"middleware": "llm_scheduler", "params": {"max_in_flight": 1}}],
    )
    second = wrap_llm_provider_pipeline(
        _StubProvider(),
        [{"middleware": "llm_scheduler", "params": {"max_in_flight": 1}}],
    )
    assert isinstance(first, PipedLLMProvider)
    assert isinstance(second, PipedLLMProvider)
    assert first._stack is not second._stack
    assert first.chat_completion(model="m", messages=[])["content"] == "ok"
    assert second.chat_completion(model="m", messages=[])["content"] == "ok"
