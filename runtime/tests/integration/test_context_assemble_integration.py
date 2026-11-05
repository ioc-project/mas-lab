#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Context assembly integration."""

from types import SimpleNamespace

from mas.runtime.boundary.context.assemble import assemble_llm_messages, has_tool_results
from mas.runtime.boundary.context.working_memory import WorkingMemoryStore
from mas.runtime.boundary.obs.operator import ObservabilityOperator
from mas.runtime.schema.observability import ObsEventKind


def test_assemble_llm_messages_includes_user_turn():
    observability = ObservabilityOperator()
    ctx = SimpleNamespace(
        working_memory=WorkingMemoryStore(),
        injected_context=[],
        memory_seeds=[],
        committed_messages=[],
        turn_history=[],
        last_user_text="hello",
        observability=observability,
        turn_index=0,
        agent_id="agent",
    )
    messages = assemble_llm_messages(
        ctx,
        manifest={"spec": {"context_manager": {"type": "stack"}}},
        tools=[{"type": "function", "function": {"name": "search", "description": "search"}}],
        resolved_model="vertex_ai/gemini-2.5-flash",
        context_window=1048576,
        completion_tokens=12000,
    )
    assert any(m.get("role") == "user" and m.get("content") == "hello" for m in messages)
    assert ctx.last_context_usage["context_window"] == 1048576
    assert ctx.last_context_usage["estimated_prompt_tokens"] > 0
    assert ctx.last_context_usage["token_breakdown"]["tool_definitions"] > 0
    event = next(event for event in observability.events if event.kind == ObsEventKind.CONTEXT_ASSEMBLED)
    assert event.payload["context_usage"]["model"] == "vertex_ai/gemini-2.5-flash"
    assert (
        event.payload["context_usage"]["estimated_prompt_tokens"] == ctx.last_context_usage["estimated_prompt_tokens"]
    )


def test_has_tool_results_detects_tool_role():
    assert has_tool_results([{"role": "tool", "content": "x"}])
    assert not has_tool_results([{"role": "user", "content": "x"}])
