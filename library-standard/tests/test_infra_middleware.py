#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Owning-package tests for infra cache / scheduler / fault middleware."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from mas.library.standard.plugins.infra.fault_inject import FaultInjectMiddleware
from mas.library.standard.plugins.infra.llm_cache import LlmCacheMiddleware
from mas.library.standard.plugins.infra.llm_scheduler import LlmSchedulerMiddleware
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn


@dataclass
class _EchoEngine:
    preview_text: str = "echo:LLM_CALL"
    calls: int = 0
    model: str = "m-a"
    api_base: str = "https://a.example/v1"
    llm_proxies: dict | None = None
    llm_proxy: dict | None = None
    ctx: object | None = None
    owns_call_identity: bool = True

    def exchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
        return self.preview_text if op == "LLM_CALL" else f"echo:{op}"

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


@dataclass
class _SilentEngine(_EchoEngine):
    preview_text: str = ""

    def exchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
        return ""


def test_fault_inject_wrap_call_kinds_overrides_class_default() -> None:
    wrapped = FaultInjectMiddleware.wrap(
        _EchoEngine(),
        {"rate": 1.0, "message": "tool fault"},
        call_kinds=("tool",),
    )
    assert wrapped.call_kinds == ("tool",)
    hit = wrapped.invoke(InvokeEngineIo(correlation_id=1, op="TOOL_CALL"))
    assert hit.response_kind == "ERROR"
    assert "tool fault" in (hit.text or "")
    miss = wrapped.invoke(InvokeEngineIo(correlation_id=1, op="LLM_CALL"))
    assert miss.response_kind == "MODEL_TEXT"


def test_fault_inject_exchange_preview_prefixes_inner() -> None:
    wrapped = FaultInjectMiddleware.wrap(_EchoEngine(), {"rate": 0.25})
    text = wrapped.exchange_preview("LLM_CALL", correlation_id=1)
    assert "[fault_inject middleware rate=0.25]" in text
    assert "echo:LLM_CALL" in text


def test_fault_inject_exchange_preview_empty_inner() -> None:
    wrapped = FaultInjectMiddleware.wrap(_SilentEngine(), {"rate": 0.0})
    assert wrapped.exchange_preview("LLM_CALL") == "[fault_inject middleware rate=0.0]"


def test_scheduler_exchange_preview_prefixes_inner() -> None:
    wrapped = LlmSchedulerMiddleware.wrap(_EchoEngine(), {"max_in_flight": 2})
    text = wrapped.exchange_preview("LLM_CALL", correlation_id=1)
    assert "llm_scheduler" in text
    assert "echo:LLM_CALL" in text


def test_scheduler_exchange_preview_empty_inner() -> None:
    wrapped = LlmSchedulerMiddleware(inner=_SilentEngine(), policy=None)
    assert wrapped.exchange_preview("LLM_CALL") == "[llm_scheduler]"


def test_llm_cache_exchange_preview_prefixes_inner() -> None:
    wrapped = LlmCacheMiddleware.wrap(_EchoEngine(), {})
    text = wrapped.exchange_preview("LLM_CALL", correlation_id=1)
    assert text.startswith("[llm_cache middleware]")
    assert "echo:LLM_CALL" in text


def test_llm_cache_exchange_preview_empty_inner() -> None:
    wrapped = LlmCacheMiddleware.wrap(_SilentEngine(), {})
    assert wrapped.exchange_preview("LLM_CALL") == "[llm_cache middleware]"


def test_llm_cache_invoke_hit_and_key(tmp_path) -> None:
    cache_path = tmp_path / "cache.json"
    inner = _EchoEngine()
    wrapped = LlmCacheMiddleware.wrap(inner, {"cache_path": str(cache_path)})
    io = InvokeEngineIo(correlation_id=1, op="LLM_CALL")
    first = wrapped.invoke(io)
    assert first.text == "live-response-1"
    assert inner.calls == 1
    assert wrapped._cache_key("echo:LLM_CALL")

    second = wrapped.invoke(io)
    assert second.cache_status == "hit"
    assert second.text == "live-response-1"
    assert inner.calls == 1


@pytest.mark.asyncio
async def test_llm_cache_ainvoke_hit(tmp_path) -> None:
    cache_path = tmp_path / "cache.json"
    inner = _EchoEngine()
    wrapped = LlmCacheMiddleware.wrap(inner, {"cache_path": str(cache_path)})
    io = InvokeEngineIo(correlation_id=2, op="LLM_CALL")
    first = await wrapped.ainvoke(io)
    assert first.text == "live-response-1"
    second = await wrapped.ainvoke(io)
    assert second.cache_status == "hit"
    assert inner.calls == 1


def test_llm_cache_raise_on_miss_records_lookup(tmp_path) -> None:
    seen: list[dict] = []

    class _Obs:
        def record_cache_lookup(self, **kwargs) -> None:
            seen.append(kwargs)

    inner = _EchoEngine(ctx=SimpleNamespace(observability=_Obs()))
    wrapped = LlmCacheMiddleware.wrap(
        inner,
        {"cache_path": str(tmp_path / "empty.json"), "raise_on_miss": True},
    )
    with pytest.raises(RuntimeError, match="llm_cache miss"):
        wrapped.invoke(InvokeEngineIo(correlation_id=9, op="LLM_CALL"))
    assert seen
    assert seen[0]["cache_layer"] == "infra_llm_cache"
    assert seen[0]["cache_status"] == "miss"


@pytest.mark.asyncio
async def test_llm_cache_ainvoke_raise_on_miss(tmp_path) -> None:
    inner = _EchoEngine()
    wrapped = LlmCacheMiddleware.wrap(
        inner,
        {"cache_path": str(tmp_path / "empty.json"), "raise_on_miss": True},
    )
    with pytest.raises(RuntimeError, match="llm_cache miss"):
        await wrapped.ainvoke(InvokeEngineIo(correlation_id=3, op="LLM_CALL"))


def test_llm_cache_copies_legacy_key_into_routed_slot(tmp_path) -> None:
    import json

    from mas.runtime.engine.llm_cache import bind_route_cache_key, legacy_preview_cache_key

    cache_path = tmp_path / "cache.json"
    preview = "echo:LLM_CALL"
    cache_path.write_text(json.dumps({legacy_preview_cache_key(preview): "old-hit"}), encoding="utf-8")
    inner = _EchoEngine(preview_text=preview)
    wrapped = LlmCacheMiddleware.wrap(inner, {"cache_path": str(cache_path)})
    out = wrapped.invoke(InvokeEngineIo(correlation_id=1, op="LLM_CALL"))
    assert out.text == "old-hit"
    assert inner.calls == 0
    stored = json.loads(cache_path.read_text(encoding="utf-8"))
    routed = bind_route_cache_key(preview, leaf="", model="m-a", api_base="https://a.example/v1")
    assert routed in stored


@pytest.mark.asyncio
async def test_llm_cache_ainvoke_copies_legacy_key_into_routed_slot(tmp_path) -> None:
    import json

    from mas.runtime.engine.llm_cache import bind_route_cache_key, legacy_preview_cache_key

    cache_path = tmp_path / "cache.json"
    preview = "echo:LLM_CALL"
    cache_path.write_text(json.dumps({legacy_preview_cache_key(preview): "old-hit"}), encoding="utf-8")
    inner = _EchoEngine(preview_text=preview)
    wrapped = LlmCacheMiddleware.wrap(inner, {"cache_path": str(cache_path)})
    out = await wrapped.ainvoke(InvokeEngineIo(correlation_id=1, op="LLM_CALL"))
    assert out.text == "old-hit"
    assert inner.calls == 0
    stored = json.loads(cache_path.read_text(encoding="utf-8"))
    routed = bind_route_cache_key(preview, leaf="", model="m-a", api_base="https://a.example/v1")
    assert routed in stored
