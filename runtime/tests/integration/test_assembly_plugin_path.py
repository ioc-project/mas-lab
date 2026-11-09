#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""End-to-end assembly through spec.assembler."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from mas.runtime.boundary.context import assemble as context_assemble
from mas.runtime.boundary.context.assemble import assemble_llm_messages
from mas.runtime.boundary.context.provider_invariant import assert_provider_payload
from mas.runtime.contracts.cm_factory import CMFactory
from mas.runtime.driver.mocks import AutoCtxAssembler


def test_legacy_custom_assembler_does_not_need_model_budget_hints(monkeypatch) -> None:
    class LegacyAssembler:
        def assemble_messages(self, ctx, *, manifest=None, correlation_id=0, tools=None):
            return [{"role": "user", "content": "legacy"}]

    monkeypatch.setattr(context_assemble, "cached_assembler", lambda *_args: LegacyAssembler())
    ctx = SimpleNamespace()
    messages = assemble_llm_messages(
        ctx,
        resolved_model="vertex_ai/gemini-2.5-flash",
        context_window=1048576,
        completion_tokens=12000,
    )
    assert messages == [{"role": "user", "content": "legacy"}]
    assert ctx.last_context_usage["context_window"] == 1048576


def _fat_tool_turn(call_id: str) -> list[dict[str, Any]]:
    return [
        {"role": "user", "content": f"ask-{call_id}-" + "q" * 200},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": call_id, "function": {"name": "f", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": call_id, "content": "r" * 200},
        {"role": "assistant", "content": "done-" + "d" * 40},
    ]


def _summarising_manifest(*, keep_turns: int = 2) -> dict[str, Any]:
    return {
        "spec": {
            "models": [{"model": "gpt-4o-mini", "context_window": 128000, "max_tokens": 2000}],
            "context_manager": {
                "type": "summarising",
                "params": {
                    "keep_turns": keep_turns,
                    "hysteresis_ratio": 0.2,
                    "trimmer": {"max_tokens": 500, "reserve_tokens": 0},
                },
            },
        }
    }


def _bind_test_summarizer(ctx: Any, manifest: dict[str, Any], summarize_fn: Any) -> None:
    cm = CMFactory.create(manifest=manifest)
    cm.bind_summarizer(summarize_fn)
    ctx._assembly_cm = cm
    ctx._assembly_cm_manifest = manifest


def test_summarising_does_not_compress_the_live_tool_round() -> None:
    seen: list[list[dict[str, Any]]] = []

    def summarize(msgs: list[dict[str, Any]]) -> str:
        seen.append(msgs)
        return "older-history"

    committed: list[dict[str, Any]] = []
    for i in range(6):
        committed.extend(_fat_tool_turn(f"old_{i}"))
    ctx = AutoCtxAssembler(last_user_text="now", committed_messages=committed)
    ctx.record_assistant_tool_call(call_id="live", tool_name="search", arguments={"q": "now"})
    ctx.record_tool_result(call_id="live", content="live-result")

    manifest = _summarising_manifest()
    _bind_test_summarizer(ctx, manifest, summarize)
    messages = assemble_llm_messages(ctx, manifest=manifest)
    assert_provider_payload(messages)
    assert any(m.get("tool_call_id") == "live" for m in messages)
    assert messages[-1]["content"] == "live-result"
    assert seen, "older committed turns must be summarized"
    dumped = json.dumps(seen)
    assert "live" not in dumped
    assert "old_0" in dumped
    tool_ids = [m.get("tool_call_id") for m in messages if m.get("role") == "tool"]
    assert "live" in tool_ids
    assert "old_5" in tool_ids
    assert "old_0" not in tool_ids


def test_hysteresis_reuses_summary_across_assemble_calls() -> None:
    calls: list[int] = []

    def summarize(msgs: list[dict[str, Any]]) -> str:
        calls.append(len(msgs))
        return "SUM"

    committed: list[dict[str, Any]] = []
    for i in range(5):
        committed.extend(_fat_tool_turn(f"old_{i}"))
    ctx = AutoCtxAssembler(last_user_text="now", committed_messages=committed)
    manifest = _summarising_manifest(keep_turns=1)
    _bind_test_summarizer(ctx, manifest, summarize)
    first = assemble_llm_messages(ctx, manifest=manifest)
    assert len(calls) == 1
    second = assemble_llm_messages(ctx, manifest=manifest)
    assert len(calls) == 1
    assert first[0]["content"] == second[0]["content"]
    assert_provider_payload(second)


def test_commit_bounds_chunks_then_assemble_still_compresses_view() -> None:
    calls: list[int] = []

    def summarize(msgs: list[dict[str, Any]]) -> str:
        calls.append(len(msgs))
        return "rolled-up"

    ctx = AutoCtxAssembler()
    for i in range(8):
        ctx.note_user_input("q" * 80 + str(i))
        ctx.note_agent_response("a" * 80)
    assert len(ctx.conversation_chunks.order) == 8

    ctx.last_user_text = "follow-up"
    manifest = _summarising_manifest(keep_turns=1)
    manifest["spec"]["context_manager"]["params"]["trimmer"] = {
        "max_tokens": 120,
        "reserve_tokens": 0,
    }
    _bind_test_summarizer(ctx, manifest, summarize)
    messages = assemble_llm_messages(ctx, manifest=manifest)
    assert_provider_payload(messages)
    assert calls
    assert any(m.get("role") == "system" and "rolled-up" in str(m.get("content")) for m in messages)


def test_tool_result_in_wm_pairs_with_committed_ask() -> None:
    """HITL / mid-turn: ask already committed, result still in working memory."""
    ctx = AutoCtxAssembler(last_user_text="")
    ctx.committed_messages = [
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call_1", "function": {"name": "search", "arguments": "{}"}}],
        },
    ]
    ctx.working_memory.messages.append({"role": "tool", "tool_call_id": "call_1", "content": "ok"})
    messages = assemble_llm_messages(ctx, manifest={"spec": {"context_manager": {"type": "stack"}}})
    assert_provider_payload(messages)
    assert [m.get("role") for m in messages[-3:]] == ["user", "assistant", "tool"]
    assert messages[-1]["content"] == "ok"


def test_parallel_live_tools_fill_missing_result_through_plugin() -> None:
    ctx = AutoCtxAssembler(last_user_text="continue")
    ctx.working_memory.record_assistant_tool_calls([("call_a", "search", {"q": "a"}), ("call_b", "search", {"q": "b"})])
    ctx.working_memory.record_tool_result(call_id="call_a", content="result-a")
    messages = assemble_llm_messages(ctx)
    assert_provider_payload(messages)
    tools = [m for m in messages if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in tools] == ["call_a", "call_b"]
    assert tools[-1]["content"] == ""


def test_tiny_token_budget_drops_history_before_live_wm() -> None:
    committed: list[dict[str, Any]] = []
    for i in range(4):
        committed.extend(_fat_tool_turn(f"old_{i}"))
    ctx = AutoCtxAssembler(last_user_text="now", committed_messages=committed)
    ctx.record_assistant_tool_call(call_id="live", tool_name="search", arguments={})
    ctx.record_tool_result(call_id="live", content="keep-me")
    manifest = {
        "spec": {
            "context_manager": {
                "type": "stack",
                "params": {"trimmer": {"max_tokens": 40, "reserve_tokens": 0}},
            }
        }
    }
    messages = assemble_llm_messages(ctx, manifest=manifest)
    assert_provider_payload(messages)
    assert any(m.get("tool_call_id") == "live" for m in messages)
    assert messages[-1]["content"] == "keep-me"


def test_wrong_tool_result_id_is_rebound_on_the_plugin_path() -> None:
    ctx = AutoCtxAssembler(last_user_text="q")
    ctx.working_memory.record_assistant_tool_calls([("call_a", "search", {}), ("call_b", "search", {})])
    ctx.working_memory.messages.append({"role": "tool", "tool_call_id": "wrong", "content": "first"})
    ctx.working_memory.messages.append({"role": "tool", "tool_call_id": "also-wrong", "content": "second"})
    messages = assemble_llm_messages(ctx)
    assert_provider_payload(messages)
    tools = [m for m in messages if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in tools] == ["call_a", "call_b"]
    assert [m["content"] for m in tools] == ["first", "second"]
