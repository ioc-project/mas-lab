#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Parity between EngineContract.invoke and ainvoke."""

from __future__ import annotations

import pytest
from mas.runtime.driver.mocks import AutoCtxAssembler
from mas.runtime.engine.llm_live import LiveLlmEngine
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
        ctx=AutoCtxAssembler(observability=_Obs()),
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
        ctx=AutoCtxAssembler(session_id="s1"),
    )
    ret = await engine.ainvoke(InvokeEngineIo(correlation_id=1, op="LLM_CALL"))
    assert ret.text == "x"


def test_a_driver_without_the_setting_leaves_the_sync_pool_size_alone() -> None:
    from mas.runtime.aio import configure_sync_threads, sync_threads
    from mas.runtime.kernel.config import KernelConfig

    assert KernelConfig().sync_threads is None
    configure_sync_threads(3)
    try:
        from mas.runtime.driver.driver import KernelDriver  # noqa: F401

        assert sync_threads() == 3
    finally:
        configure_sync_threads(1)


def test_a_wrapped_sync_provider_survives_a_copy() -> None:
    import copy

    from mas.runtime.registry.llm_provider_protocol import SyncLLMProvider

    class P:
        def chat_completion(self, **_):
            return {}

    assert copy.copy(SyncLLMProvider(P())) is not None
