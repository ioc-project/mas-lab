#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A denied call in a parallel batch returns to the model; siblings still run."""

from __future__ import annotations

import pytest
from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.engine.simulated import SimulatedEngine
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.coupling import GovDecision
from mas.runtime.schema.ingress import EngineIoReturn, ToolCallSpec


class _BlockBeta:
    def evaluate_egress(self, intent, *, config):  # noqa: ANN001, ARG002
        if intent.op == "TOOL_CALL" and intent.tool_name == "beta":
            return GovDecision.BLOCK, "block-beta", "beta is denied"
        return GovDecision.ALLOW, "allow", "ok"


class _ParallelEngine(SimulatedEngine):
    def __init__(self) -> None:
        super().__init__()
        self.tool_calls: list[str] = []
        self._asked = False

    def invoke(self, io):
        if io.op == "LLM_CALL" and not self._asked:
            self._asked = True
            return EngineIoReturn(
                correlation_id=io.correlation_id,
                response_kind="MODEL_TEXT",
                next_step="PARALLEL_TOOL_CALLS",
                parallel_tools=(
                    ToolCallSpec(tool_name="alpha", tool_arguments={}),
                    ToolCallSpec(tool_name="beta", tool_arguments={}),
                    ToolCallSpec(tool_name="gamma", tool_arguments={}),
                ),
                offered_tools=["alpha", "beta", "gamma"],
            )
        if io.op == "LLM_CALL":
            return EngineIoReturn(
                correlation_id=io.correlation_id,
                response_kind="MODEL_TEXT",
                next_step="STOP",
                text="adapted after denial",
            )
        if io.op == "TOOL_CALL":
            self.tool_calls.append(str(io.correlation_id))
            return EngineIoReturn(
                correlation_id=io.correlation_id,
                response_kind="TOOL_RESULT",
                next_step="LLM_CALL",
                text=f"ran-{io.correlation_id}",
            )
        return EngineIoReturn(correlation_id=io.correlation_id, response_kind="ERROR", next_step="STOP")


def _run_parts() -> tuple[_ParallelEngine, RuntimeInstance]:
    engine = _ParallelEngine()
    inst = RuntimeInstance.from_parts(
        engine=engine,
        config=KernelConfig(egress_governance_plugin=_BlockBeta()),
    )
    inst.capture_session_baseline()
    return engine, inst


def _assert_denial_fed_back(engine: _ParallelEngine, inst: RuntimeInstance, trace) -> None:
    assert not any(getattr(err, "code", "") == "GOV_BLOCK" for err in trace.boundary_errors)
    assert len(engine.tool_calls) == 2
    results = [ev.text for ev in inst.kernel.run.events if ev.response_kind == "TOOL_RESULT"]
    assert any("beta is denied" in (t or "") for t in results)
    assert any((t or "").startswith("ran-") for t in results)
    assert trace.client_responses
    assert "adapted after denial" in (trace.client_responses[-1].content or "")


def test_parallel_batch_denial_is_fed_back_not_fatal() -> None:
    engine, inst = _run_parts()
    _assert_denial_fed_back(engine, inst, inst.run_user_text("call three tools"))


@pytest.mark.asyncio
async def test_parallel_batch_denial_is_fed_back_on_async_path() -> None:
    engine, inst = _run_parts()
    _assert_denial_fed_back(engine, inst, await inst.arun_user_text("call three tools"))


class _CountingEngine(_ParallelEngine):
    def __init__(self) -> None:
        super().__init__()
        self.llm_calls = 0

    def invoke(self, io):
        if io.op == "LLM_CALL":
            self.llm_calls += 1
        return super().invoke(io)


def _sequential(plugin) -> tuple[_CountingEngine, RuntimeInstance]:
    engine = _CountingEngine()
    inst = RuntimeInstance.from_parts(
        engine=engine,
        config=KernelConfig(parallel_tool_calls=False, egress_governance_plugin=plugin),
    )
    inst.capture_session_baseline()
    return engine, inst


class _AllowAll:
    def evaluate_egress(self, intent, *, config):  # noqa: ANN001, ARG002
        return GovDecision.ALLOW, "allow", "ok"


def test_parallel_off_runs_every_call_one_at_a_time_and_asks_the_model_once() -> None:
    engine, inst = _sequential(_AllowAll())
    trace = inst.run_user_text("call three tools")
    assert len(engine.tool_calls) == 3
    assert engine.llm_calls == 2  # the request, then one answer after all three results
    assert "adapted after denial" in (trace.client_responses[-1].content or "")
    assert inst.kernel.q.queued_tools == []


def test_parallel_off_denial_is_that_calls_result_and_the_rest_still_run() -> None:
    engine, inst = _sequential(_BlockBeta())
    trace = inst.run_user_text("call three tools")
    assert len(engine.tool_calls) == 2
    assert engine.llm_calls == 2
    results = [ev.text for ev in inst.kernel.run.events if ev.response_kind == "TOOL_RESULT"]
    assert any("beta is denied" in (t or "") for t in results)
    assert not any(getattr(err, "code", "") == "GOV_BLOCK" for err in trace.boundary_errors)


def test_parallel_off_all_denied_asks_the_model_once() -> None:
    class _BlockAll:
        def evaluate_egress(self, intent, *, config):  # noqa: ANN001, ARG002
            if intent.op == "TOOL_CALL":
                return GovDecision.BLOCK, "b", "no tools"
            return GovDecision.ALLOW, "allow", "ok"

    engine, inst = _sequential(_BlockAll())
    trace = inst.run_user_text("call three tools")
    assert engine.tool_calls == []
    assert engine.llm_calls == 2
    assert "adapted after denial" in (trace.client_responses[-1].content or "")


def test_parallel_off_hitl_pause_keeps_the_rest_queued_and_resumes() -> None:
    from mas.runtime.schema.hitl import HitlResolveChoice
    from mas.runtime.schema.ingress import HitlResolve

    class _HitlBeta:
        asking = True

        def evaluate_egress(self, intent, *, config):  # noqa: ANN001, ARG002
            if intent.op == "TOOL_CALL" and intent.tool_name == "beta" and self.asking:
                return GovDecision.HITL, "hitl", "ask"
            return GovDecision.ALLOW, "allow", "ok"

    plugin = _HitlBeta()
    engine, inst = _sequential(plugin)
    trace = inst.run_user_text("call three tools")
    assert len(engine.tool_calls) == 1  # alpha ran, beta is waiting
    assert len(inst.kernel.q.queued_tools) == 1  # gamma waits behind it
    request = trace.hitl_requests[-1]
    plugin.asking = False
    after = inst.feed(HitlResolve(request_id=request.request_id, resolution=HitlResolveChoice.ALLOW))
    assert len(engine.tool_calls) == 3
    assert inst.kernel.q.queued_tools == []
    assert after.client_responses


class _WideEngine(SimulatedEngine):
    """Asks for ``width`` tools in one reply, then answers once they all returned."""

    def __init__(self, width: int) -> None:
        super().__init__()
        self.width = width
        self.asked = False
        self.ran: list[str] = []

    def invoke(self, io):
        if io.op == "LLM_CALL" and not self.asked:
            self.asked = True
            names = [f"t{i}" for i in range(self.width)]
            return EngineIoReturn(
                correlation_id=io.correlation_id,
                response_kind="MODEL_TEXT",
                next_step="PARALLEL_TOOL_CALLS",
                parallel_tools=tuple(ToolCallSpec(tool_name=n, tool_arguments={}) for n in names),
                offered_tools=names,
            )
        if io.op == "LLM_CALL":
            return EngineIoReturn(
                correlation_id=io.correlation_id, response_kind="MODEL_TEXT", next_step="STOP", text="final"
            )
        self.ran.append(str(io.correlation_id))
        return EngineIoReturn(
            correlation_id=io.correlation_id, response_kind="TOOL_RESULT", next_step="LLM_CALL", text="ran"
        )


@pytest.mark.parametrize("depth", [2, 32])
def test_a_batch_larger_than_the_queue_depth_runs_in_chunks(depth: int) -> None:
    engine = _WideEngine(width=40)
    instance = RuntimeInstance.from_parts(
        engine=engine, config=KernelConfig(parallel_tool_calls=True, engine_queue_depth=depth)
    )
    instance.capture_session_baseline()
    turn = instance.run_user_text("go")
    assert turn.boundary_errors == []
    assert len(engine.ran) == 40
    assert [r.content for r in turn.client_responses] == ["final"]
    assert not instance.kernel.q.pending_tools_by_cid
