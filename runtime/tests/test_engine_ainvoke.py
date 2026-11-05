#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Parity between EngineContract.invoke and ainvoke."""

from __future__ import annotations

import pytest
from mas.runtime.engine.llm_live import LiveLlmEngine
from mas.runtime.engine.simulated import SimulatedEngine
from mas.runtime.schema.egress import InvokeEngineIo


class _StubProvider:
    kind = "openai"

    def __init__(self, message: dict) -> None:
        self.message = message
        self.sync_calls = 0
        self.async_calls = 0

    def chat_completion(self, **kwargs):
        self.sync_calls += 1
        self.last_kwargs = kwargs
        return dict(self.message)

    async def achat_completion(self, **kwargs):
        self.async_calls += 1
        self.last_kwargs = kwargs
        return dict(self.message)


@pytest.mark.asyncio
async def test_simulated_engine_ainvoke_matches_invoke():
    engine = SimulatedEngine(stop_text="hello")
    io = InvokeEngineIo(correlation_id=2, op="LLM_CALL")
    sync = engine.invoke(io)
    async_ret = await engine.ainvoke(io)
    assert sync == async_ret
    assert async_ret.text == "hello"


@pytest.mark.asyncio
async def test_live_engine_ainvoke_parity_with_invoke():
    message = {"role": "assistant", "content": "same-answer", "finish_reason": "stop"}
    provider = _StubProvider(message)
    engine = LiveLlmEngine(llm_provider=provider, use_cache=False, model="stub")
    io = InvokeEngineIo(correlation_id=1, op="LLM_CALL")
    sync = engine.invoke(io)
    async_ret = await engine.ainvoke(io)
    assert sync.text == async_ret.text == "same-answer"
    assert provider.sync_calls == 1
    assert provider.async_calls == 1


@pytest.mark.asyncio
async def test_live_engine_ainvoke_rejects_provider_without_achat_completion():
    class SyncOnly:
        kind = "openai"

        def chat_completion(self, **kwargs):
            return {"content": "nope"}

    engine = LiveLlmEngine(llm_provider=SyncOnly(), use_cache=False, model="stub")
    ret = await engine.ainvoke(InvokeEngineIo(correlation_id=1, op="LLM_CALL"))
    assert ret.response_kind == "ERROR"
    assert "achat_completion" in (ret.text or "")


@pytest.mark.asyncio
async def test_achat_completion_stream_emits_llm_delta_and_assembles():
    seen: list[str] = []

    class _StreamProvider:
        kind = "openai"

        def chat_completion(self, **kwargs):
            return {"role": "assistant", "content": "ab", "finish_reason": "stop"}

        async def achat_completion(self, **kwargs):
            return {"role": "assistant", "content": "ab", "finish_reason": "stop"}

        async def achat_completion_stream(self, **kwargs):
            yield {"delta": "a"}
            yield {"delta": "b"}

    class _Obs:
        def record_session(self, kind: str, **fields):
            if kind == "llm_delta":
                seen.append(str(fields.get("text") or ""))

    engine = LiveLlmEngine(
        llm_provider=_StreamProvider(),
        use_cache=False,
        model="stub",
        stream=True,
        ctx=type("C", (), {"observability": _Obs()})(),
    )
    ret = await engine.ainvoke(InvokeEngineIo(correlation_id=1, op="LLM_CALL"))
    assert ret.text == "ab"
    assert seen == ["a", "b"]


@pytest.mark.asyncio
async def test_ainvoke_uses_stream_iterator_even_when_spec_stream_is_false() -> None:
    class _StreamProvider:
        kind = "openai"

        def chat_completion(self, **kwargs):
            return {"role": "assistant", "content": "no", "finish_reason": "stop"}

        async def achat_completion(self, **kwargs):
            raise AssertionError("one-shot async must not run when a stream iterator exists")

        async def achat_completion_stream(self, **kwargs):
            yield {"delta": "x"}

    engine = LiveLlmEngine(
        llm_provider=_StreamProvider(),
        use_cache=False,
        model="stub",
        stream=False,
        ctx=type("C", (), {"session_id": "s1", "observability": None})(),
    )
    ret = await engine.ainvoke(InvokeEngineIo(correlation_id=1, op="LLM_CALL"))
    assert ret.text == "x"
