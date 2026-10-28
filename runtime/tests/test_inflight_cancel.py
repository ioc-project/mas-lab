#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
import asyncio

import pytest
from mas.runtime.driver.driver import KernelDriver
from mas.runtime.engine.inflight_llm import has_inflight
from mas.runtime.kernel.orchestrator import RuntimeKernel
from mas.runtime.schema.egress import InvokeEngineIo


class _HangEngine:
    async def ainvoke(self, io):
        await asyncio.sleep(30)
        raise AssertionError("should have been cancelled")


@pytest.mark.asyncio
async def test_cancel_inflight_llm_returns_cancelled_finish_reason() -> None:
    driver = KernelDriver(kernel=RuntimeKernel(), engine=_HangEngine(), session_id="s-cancel")
    io = InvokeEngineIo(correlation_id=1, op="LLM_CALL")
    task = asyncio.create_task(driver._ainvoke_engine(io))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if has_inflight("s-cancel"):
            break
    assert has_inflight("s-cancel")
    from mas.runtime.engine.inflight_llm import cancel

    assert cancel("s-cancel") is True
    ret = await asyncio.wait_for(task, timeout=2)
    assert ret.finish_reason == "cancelled"
    assert ret.response_kind == "ERROR"
    assert has_inflight("s-cancel") is False


class _PartialHangEngine:
    async def ainvoke(self, io):
        from mas.runtime.engine.inflight_llm import append_partial

        append_partial("s-steer", "Hello ")
        await asyncio.sleep(30)
        raise AssertionError("should have been preempted")


@pytest.mark.asyncio
async def test_steer_keeps_partial_tokens() -> None:
    from mas.runtime.engine.inflight_llm import peek_preempt, request_preempt
    from mas.runtime.schema.ingress import EngineIoReturn, OperatorSteerReceived

    driver = KernelDriver(kernel=RuntimeKernel(), engine=_PartialHangEngine(), session_id="s-steer")
    io = InvokeEngineIo(correlation_id=1, op="LLM_CALL")
    task = asyncio.create_task(driver._ainvoke_engine(io))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if has_inflight("s-steer"):
            break
    assert has_inflight("s-steer")
    assert request_preempt("s-steer", "Answer Lyon.", source="peer", sender="planner") is True
    ret = await asyncio.wait_for(task, timeout=2)
    assert isinstance(ret, EngineIoReturn)
    assert ret.finish_reason == "preempted"
    assert ret.response_kind == "MODEL_TEXT"
    assert ret.text == "Hello "
    assert peek_preempt("s-steer") == "Answer Lyon."
    followed = driver._with_pending_steer([ret])
    assert followed[0] is ret
    assert isinstance(followed[1], OperatorSteerReceived)
    assert followed[1].context_text == "Answer Lyon."
    assert followed[1].source == "peer"
    assert followed[1].sender == "planner"
    assert followed[1].intent == "steer"
    assert peek_preempt("s-steer") is None


@pytest.mark.asyncio
async def test_replace_discards_partial_tokens() -> None:
    from mas.runtime.engine.inflight_llm import peek_replace, request_replace
    from mas.runtime.schema.ingress import EngineIoReturn, UserInputReceived

    driver = KernelDriver(kernel=RuntimeKernel(), engine=_PartialHangEngine(), session_id="s-steer")
    io = InvokeEngineIo(correlation_id=1, op="LLM_CALL")
    task = asyncio.create_task(driver._ainvoke_engine(io))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if has_inflight("s-steer"):
            break
    assert request_replace("s-steer", "Start over in Lyon.") is True
    ret = await asyncio.wait_for(task, timeout=2)
    assert isinstance(ret, EngineIoReturn)
    assert ret.finish_reason == "replaced"
    assert ret.text == ""
    assert peek_replace("s-steer") == "Start over in Lyon."
    followed = driver._with_pending_steer([ret])
    assert isinstance(followed[1], UserInputReceived)
    assert followed[1].text == "Start over in Lyon."
    assert followed[1].source == ""
    assert peek_replace("s-steer") is None


@pytest.mark.asyncio
async def test_preempt_live_stream_keeps_prefix_and_does_not_finish() -> None:
    from mas.runtime.engine.inflight_llm import peek_preempt, request_preempt
    from mas.runtime.engine.llm_live import LiveLlmEngine
    from mas.runtime.schema.ingress import EngineIoReturn, OperatorSteerReceived

    class _HangStream:
        kind = "openai"

        def chat_completion(self, **kwargs):
            return {"role": "assistant", "content": "no", "finish_reason": "stop"}

        async def achat_completion(self, **kwargs):
            raise AssertionError("one-shot async must not run")

        async def achat_completion_stream(self, **kwargs):
            yield {"delta": "Hello "}
            await asyncio.sleep(30)

    ctx = type("C", (), {"session_id": "s-live", "observability": None})()
    engine = LiveLlmEngine(llm_provider=_HangStream(), use_cache=False, model="stub", stream=False, ctx=ctx)
    driver = KernelDriver(kernel=RuntimeKernel(), engine=engine, session_id="s-live")
    io = InvokeEngineIo(correlation_id=1, op="LLM_CALL")
    task = asyncio.create_task(driver._ainvoke_engine(io))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if has_inflight("s-live"):
            break
    assert has_inflight("s-live")
    assert request_preempt("s-live", "Answer Lyon.") is True
    ret = await asyncio.wait_for(task, timeout=2)
    assert isinstance(ret, EngineIoReturn)
    assert ret.finish_reason == "preempted"
    assert ret.text == "Hello "
    assert peek_preempt("s-live") == "Answer Lyon."
    followed = driver._with_pending_steer([ret])
    assert isinstance(followed[1], OperatorSteerReceived)
    assert followed[1].context_text == "Answer Lyon."
