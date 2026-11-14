#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A tool the agent was not given is an observation, and the offered names are traced."""

from __future__ import annotations

from mas.runtime.boundary.context.assemble import assemble_llm_messages
from mas.runtime.boundary.obs.operator import ObservabilityOperator
from mas.runtime.driver.mocks import AutoCtxAssembler
from mas.runtime.engine.llm_live import LiveLlmEngine
from mas.runtime.registry.provider_protocol import ManifestToolLoadError
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.observability import ObsEventKind


class _LogsOnly:
    def call_tool(self, tool_name, arguments, **kwargs):
        raise ManifestToolLoadError(f"Tool {tool_name!r} not found in manifest or overlays")


def test_unknown_tool_returns_to_the_same_agent() -> None:
    engine = LiveLlmEngine(use_tool_loop=True, use_cache=False, tool_provider=_LogsOnly())
    engine._offered_tool_names = ["get_logs", "get_metrics"]
    engine.set_scheduled_tool("get_deployments", {})

    ret = engine.invoke(InvokeEngineIo(correlation_id=4, op="TOOL_CALL"))

    assert ret.response_kind == "TOOL_RESULT"
    assert ret.next_step == "LLM_CALL"
    assert "Tool 'get_deployments' not found in manifest or overlays" in ret.text
    assert "Available tools: get_logs, get_metrics." in ret.text


def test_context_assembled_records_offered_tool_names() -> None:
    obs = ObservabilityOperator()
    ctx = AutoCtxAssembler(observability=obs, last_user_text="check latency", agent_id="telemetry")
    tools = [
        {
            "type": "function",
            "function": {"name": "get_logs", "description": "logs", "parameters": {"type": "object"}},
        },
        {
            "type": "function",
            "function": {"name": "get_metrics", "description": "metrics", "parameters": {"type": "object"}},
        },
    ]

    assemble_llm_messages(ctx, correlation_id=6, tools=tools)

    assembled = [ev for ev in obs.events if ev.kind == ObsEventKind.CONTEXT_ASSEMBLED]
    assert assembled[-1].payload["tools"] == ["get_logs", "get_metrics"]
    assert assembled[-1].payload["agent_id"] == "telemetry"

    obs.record_engine_io(
        correlation_id=6,
        op="LLM_CALL",
        tools=["get_logs", "get_metrics"],
    )
    obs.record_engine_io_return(
        correlation_id=6,
        op="LLM_CALL",
        text="",
        next_step="TOOL_CALL",
        response_kind="MODEL_TEXT",
        tool_calls=[{"toolName": "get_deployments", "arguments": {}}],
    )
    from mas.library.standard.lib.observability.native.transform import (
        NativeObservabilityTransform,
        TransformContext,
    )

    transform = NativeObservabilityTransform()
    tctx = TransformContext(agent_id="telemetry")
    native: list[dict] = []
    for ev in obs.events:
        payload = ev.model_dump(mode="json")
        payload["_source"] = "boundary"
        native.extend(transform.transform(payload, ctx=tctx))

    context_events = [row for row in native if row["kind"] == "context_assembled"]
    llm_starts = [row for row in native if row["kind"] == "llm_call_start"]
    llm_ends = [row for row in native if row["kind"] == "llm_call_end"]
    assert context_events[-1]["tools"] == ["get_logs", "get_metrics"]
    assert llm_starts[-1]["tools"] == ["get_logs", "get_metrics"]
    assert "tools" not in llm_ends[-1]
    assert llm_ends[-1]["tool_calls"] == [{"toolName": "get_deployments", "arguments": {}}]
