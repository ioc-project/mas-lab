#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Committed conversation chunk graph (append / project / serialize)."""

from __future__ import annotations

import json

from mas.runtime.boundary.context.assemble import assemble_llm_messages
from mas.runtime.boundary.context.conversation_chunks import (
    ConversationChunkStore,
    SummaryChunk,
)
from mas.runtime.boundary.context.provider_invariant import assert_provider_payload
from mas.runtime.boundary.context.working_memory_registry import (
    WorkingMemorySnapshot,
    restore_ctx,
    snapshot_ctx,
)
from mas.runtime.driver.mocks import AutoCtxAssembler


def _turn(user: str, assistant: str) -> list[dict]:
    return [
        {"role": "user", "content": user},
        {"role": "assistant", "content": assistant},
    ]


def _tool_turn(call_id: str = "call_1") -> list[dict]:
    return [
        {"role": "user", "content": "ask"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": call_id, "function": {"name": "search", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": call_id, "content": "result"},
        {"role": "assistant", "content": "done"},
    ]


def test_append_and_project_message_chunks() -> None:
    store = ConversationChunkStore()
    store.append_turn(_turn("q1", "a1"))
    store.append_turn(_turn("q2", "a2"))
    projected = store.project_messages()
    assert len(projected) == 4
    assert projected[0]["content"] == "q1"
    assert projected[-1]["content"] == "a2"


def test_empty_append_returns_empty_id() -> None:
    store = ConversationChunkStore()
    assert store.append_turn([]) == ""
    assert store.project_messages() == []


def test_project_summary_before_recent_chunks() -> None:
    store = ConversationChunkStore()
    store.append_turn(_turn("old", "a"))
    store.summary_chunk_id = "sum_1"
    store.chunks["sum_1"] = SummaryChunk(
        chunk_id="sum_1",
        text="rolled",
        child_chunk_ids=[],
    )
    store.append_turn(_turn("new", "b"))
    projected = store.project_messages()
    assert projected[0]["role"] == "system"
    assert projected[-1]["content"] == "b"


def test_serialization_round_trip() -> None:
    store = ConversationChunkStore()
    store.append_turn(_turn("u", "a"))
    store.append_turn(_turn("v", "b"))
    restored = ConversationChunkStore.from_dict(store.to_dict())
    assert restored.order == store.order
    assert set(restored.chunks) == set(store.chunks)
    assert restored.project_messages() == store.project_messages()


def test_from_dict_none_returns_empty_store() -> None:
    store = ConversationChunkStore.from_dict(None)
    assert store.order == []
    assert store.chunks == {}


def test_assembly_uses_chunk_projection_over_flat_committed() -> None:
    ctx = AutoCtxAssembler(last_user_text="follow-up")
    ctx.committed_messages = [{"role": "user", "content": "stale"}]
    ctx.conversation_chunks.append_turn(_turn("from-chunk", "answer"))
    messages = assemble_llm_messages(ctx)
    assert any(m.get("content") == "from-chunk" for m in messages)
    assert not any(m.get("content") == "stale" for m in messages)
    assert messages[-1]["content"] == "follow-up"


def test_assembly_chunk_projection_is_provider_safe() -> None:
    ctx = AutoCtxAssembler(last_user_text="next")
    ctx.conversation_chunks.append_turn(_tool_turn("call_a"))
    ctx.conversation_chunks.append_turn(_tool_turn("call_b"))
    messages = assemble_llm_messages(ctx)
    assert_provider_payload(messages)


def test_assembly_does_not_rewrite_chunks() -> None:
    ctx = AutoCtxAssembler(last_user_text="q")
    ctx.conversation_chunks.append_turn(_turn("a", "b" * 500))
    assemble_llm_messages(ctx)
    assemble_llm_messages(ctx)
    assert not ctx.conversation_chunks.summary_chunk_id
    assert len(ctx.conversation_chunks.order) == 1


def test_turn_commit_keeps_chunks_while_under_keep_turns() -> None:
    ctx = AutoCtxAssembler()
    for i in range(4):
        ctx.note_user_input(f"q{i}")
        ctx.note_agent_response("x" * 200)
    assert len(ctx.conversation_chunks.order) == 4
    projected = ctx.conversation_chunks.project_messages()
    assert projected[0]["role"] == "user"
    assert projected[0]["content"] == "q0"


def test_turn_commit_folds_when_under_token_budget_keeps_archive() -> None:
    """Short chats never fold. Archive still holds every leaf."""
    ctx = AutoCtxAssembler(
        manifest={
            "spec": {
                "context_manager": {
                    "type": "summarising",
                    "params": {"keep_turns": 2, "hysteresis_ratio": 0, "summarizer": "drop"},
                }
            }
        }
    )
    for i in range(6):
        ctx.note_user_input(f"q{i}")
        ctx.note_agent_response(f"a{i}")
    users = [m.get("content") for m in ctx.committed_messages if m.get("role") == "user"]
    assert users == [f"q{i}" for i in range(6)]
    store = ctx.conversation_chunks
    assert len(store.order) == 6
    assert store.covers_archive()
    blob = json.dumps(snapshot_ctx(ctx).conversation_chunks)
    assert "q0" in blob
    assert "q5" in blob


def test_turn_commit_default_keeps_full_short_chat() -> None:
    ctx = AutoCtxAssembler()
    for i in range(12):
        ctx.note_user_input(f"q{i}")
        ctx.note_agent_response(f"a{i}")
    contents = [m.get("content") for m in ctx.committed_messages]
    assert "q0" in contents
    assert "q11" in contents
    assert len(ctx.conversation_chunks.order) == 12
    assert ctx.conversation_chunks.covers_archive()


def test_turn_commit_bounds_sliding_window() -> None:
    ctx = AutoCtxAssembler(
        manifest={
            "spec": {
                "context_manager": {
                    "type": "sliding-window",
                    "params": {"keep_turns": 3},
                }
            }
        }
    )
    for i in range(8):
        ctx.note_user_input(f"q{i}")
        ctx.note_agent_response(f"a{i}")
    users = [m.get("content") for m in ctx.committed_messages if m.get("role") == "user"]
    assert users == ["q5", "q6", "q7"]
    store = ctx.conversation_chunks
    assert store.covers_archive()
    assert len([c for c in store.chunks.values() if getattr(c, "kind", "") == "messages"]) == 8
    assert [m.get("content") for m in store.project_messages() if m.get("role") == "user"] == [
        "q5",
        "q6",
        "q7",
    ]


def test_turn_commit_bounds_stack_max_messages() -> None:
    ctx = AutoCtxAssembler(
        manifest={
            "spec": {
                "context_manager": {
                    "type": "stack",
                    "params": {"max_messages": 4},
                }
            }
        }
    )
    for i in range(6):
        ctx.note_user_input(f"q{i}")
        ctx.note_agent_response(f"a{i}")
    assert len(ctx.committed_messages) == 4
    contents = [m.get("content") for m in ctx.committed_messages]
    assert "q0" not in contents
    assert "q5" in contents
    store = ctx.conversation_chunks
    assert store.covers_archive()
    assert "q0" in json.dumps(store.to_dict())


def test_tool_trajectory_preserved_in_message_chunk() -> None:
    ctx = AutoCtxAssembler()
    ctx.note_user_input("Who is POTUS?")
    ctx.record_assistant_tool_call(call_id="call_1", tool_name="search", arguments={})
    ctx.record_tool_result(call_id="call_1", content="Donald Trump")
    ctx.note_agent_response("Donald Trump is POTUS.")
    chunk_ids = [cid for cid in ctx.conversation_chunks.order]
    assert len(chunk_ids) == 1
    chunk = ctx.conversation_chunks.chunks[chunk_ids[0]]
    assert any(m.get("role") == "tool" for m in chunk.messages)
    assert_provider_payload(chunk.messages)


def test_snapshot_round_trips_chunk_store() -> None:
    ctx = AutoCtxAssembler()
    ctx.note_user_input("hello")
    ctx.note_agent_response("world")
    snap = snapshot_ctx(ctx)
    other = AutoCtxAssembler()
    restore_ctx(other, snap)
    assert other.conversation_chunks.project_messages() == ctx.conversation_chunks.project_messages()


def test_working_memory_snapshot_carries_chunks_dict() -> None:
    ctx = AutoCtxAssembler()
    ctx.note_user_input("a")
    ctx.note_agent_response("b")
    snap = snapshot_ctx(ctx)
    assert snap.conversation_chunks is not None
    assert snap.conversation_chunks["order"]


def test_restore_empty_snapshot_clears_chunks() -> None:
    ctx = AutoCtxAssembler()
    ctx.note_user_input("a")
    ctx.note_agent_response("b")
    restore_ctx(ctx, WorkingMemorySnapshot())
    assert ctx.conversation_chunks.order == []


def test_multi_session_chunk_stores_are_independent() -> None:
    ctx_a = AutoCtxAssembler(session_id="a")
    ctx_b = AutoCtxAssembler(session_id="b")
    ctx_a.note_user_input("A")
    ctx_a.note_agent_response("a")
    ctx_b.note_user_input("B")
    ctx_b.note_agent_response("b")
    assert ctx_a.conversation_chunks.order != ctx_b.conversation_chunks.order
    msgs_a = assemble_llm_messages(ctx_a, manifest={"spec": {}})
    msgs_b = assemble_llm_messages(ctx_b, manifest={"spec": {}})
    assert any("A" in str(m.get("content")) for m in msgs_a)
    assert any("B" in str(m.get("content")) for m in msgs_b)
