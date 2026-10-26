#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Covering-set ids stay internal; the model request is chat messages."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from mas.library.standard.lib.context.committed_history import (
    apply_request_view,
    compact_committed_history,
    request_history,
)
from mas.library.standard.lib.context.payload import tool_group_len
from mas.library.standard.lib.context.spec import configure_archive_store
from mas.library.standard.lib.context.working_memory import working_memory_slice_limit
from mas.library.standard.plugins.context.assembler import ContextAssemblerPlugin
from mas.runtime.boundary.context.archive import ConversationChunkStore


class _KeepTail:
    """Context manager that keeps the last two rows when history is long."""

    def manage_history(self, past: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
        _ = budget
        return list(past[-2:] if len(past) > 2 else past)


def _turn(user: str, assistant: str) -> list[dict[str, Any]]:
    return [
        {"role": "user", "content": user},
        {"role": "assistant", "content": assistant},
    ]


def _ctx(**fields: Any) -> SimpleNamespace:
    store = fields.pop("store", ConversationChunkStore())
    return SimpleNamespace(
        conversation_chunks=store,
        committed_messages=fields.pop("committed_messages", []),
        turn_history=fields.pop("turn_history", []),
        last_user_text=fields.pop("last_user_text", ""),
        injected_context=[],
        memory_seeds=[],
        working_memory=None,
        archive_dir=fields.pop("archive_dir", ""),
        archive_watermark_bytes=fields.pop("archive_watermark_bytes", None),
        **fields,
    )


def test_request_history_projects_chat_rows_not_chunk_ids() -> None:
    store = ConversationChunkStore()
    store.append_turn(_turn("q0", "a0"))
    store.append_turn(_turn("q1", "a1"))
    messages = request_history(_ctx(store=store))
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    assert [m["content"] for m in messages] == ["q0", "a0", "q1", "a1"]
    assert all("chunk_id" not in m for m in messages)
    assert store.order
    assert all(cid not in str(messages) for cid in store.order)


def test_request_history_falls_back_to_committed_then_turn_history() -> None:
    empty = ConversationChunkStore()
    committed = request_history(
        _ctx(store=empty, committed_messages=[{"role": "user", "content": "kept"}])
    )
    assert committed == [{"role": "user", "content": "kept"}]
    from_turns = request_history(
        _ctx(store=empty, committed_messages=[], turn_history=[("q", "a"), ("later", "   ")])
    )
    assert from_turns == [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "later"},
    ]


def test_apply_request_view_leaves_non_list_turn_history_alone() -> None:
    ctx = _ctx(committed_messages=None, turn_history="opaque")
    apply_request_view(ctx, _turn("q", "a"))
    assert ctx.committed_messages == _turn("q", "a")
    assert ctx.turn_history == "opaque"


def test_apply_request_view_folds_covering_set_and_trims_turn_history() -> None:
    store = ConversationChunkStore()
    store.append_turn(_turn("q0", "a0"))
    store.append_turn(_turn("q1", "a1"))
    store.append_turn(_turn("q2", "a2"))
    ctx = _ctx(store=store, turn_history=[("q0", "a0"), ("q1", "a1"), ("q2", "a2")])
    apply_request_view(ctx, _turn("q2", "a2"))
    assert [m["content"] for m in store.project_messages()] == ["q2", "a2"]
    assert ctx.turn_history == [("q2", "a2")]
    apply_request_view(ctx, [])
    assert ctx.turn_history == []


def test_compact_committed_history_is_noop_until_the_window_is_exceeded() -> None:
    store = ConversationChunkStore()
    store.append_turn(_turn("q0", "a0"))
    ctx = _ctx(store=store)
    ctx._assembly_cm = _KeepTail()
    ctx._assembly_cm_manifest = None
    covering = list(store.order)
    compact_committed_history(ctx)
    assert store.order == covering
    store.append_turn(_turn("q1", "a1"))
    store.append_turn(_turn("q2", "a2"))
    compact_committed_history(ctx)
    assert [m["content"] for m in request_history(ctx)] == ["q2", "a2"]
    compact_committed_history(_ctx(store=ConversationChunkStore()))
    plugin_ctx = _ctx(store=ConversationChunkStore())
    plugin_ctx._assembly_cm = _KeepTail()
    plugin_ctx._assembly_cm_manifest = None
    ContextAssemblerPlugin().compact_committed_history(plugin_ctx)


def test_configure_archive_store_uses_ctx_path_and_skips_missing_configure(tmp_path: Path) -> None:
    store = ConversationChunkStore()
    archive = tmp_path / "from-ctx"
    configure_archive_store(
        store,
        _ctx(store=store, archive_dir=str(archive), archive_watermark_bytes=8),
        manifest={"spec": {"context_manager": {"params": {"archive": {"path": str(tmp_path / "from-spec")}}}}},
    )
    assert store.persist_dir == str(archive)
    assert store.memory_watermark_bytes == 8
    configure_archive_store(SimpleNamespace(), _ctx(archive_dir=str(tmp_path / "ignored")))


def test_working_memory_slice_limit_ignores_manifest_count() -> None:
    assert working_memory_slice_limit(None) == 0
    assert working_memory_slice_limit({"spec": {"context_manager": {"params": {"working_memory_messages": 12}}}}) == 0


def test_tool_group_len_is_zero_outside_the_list() -> None:
    assert tool_group_len([]) == 0
    assert tool_group_len([{"role": "user", "content": "q"}], index=3) == 0


def test_assembler_sends_projected_messages_and_updates_covering_set() -> None:
    store = ConversationChunkStore()
    store.append_turn(_turn("q0", "a0"))
    store.append_turn(_turn("q1", "a1"))
    store.append_turn(_turn("q2", "a2"))
    ctx = _ctx(store=store, last_user_text="now")
    ctx._assembly_cm = _KeepTail()
    ctx._assembly_cm_manifest = None
    messages = ContextAssemblerPlugin().assemble_messages(ctx)
    contents = [m["content"] for m in messages if m.get("role") in {"user", "assistant"}]
    assert "q0" not in contents
    assert contents[-3:] == ["q2", "a2", "now"]
    assert all(cid not in str(messages) for cid in store.order)
    assert [m["content"] for m in store.project_messages()] == ["q2", "a2"]
