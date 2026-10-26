#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Conversation archive: covering views, nested summaries, disk spill."""

from __future__ import annotations

from pathlib import Path

from mas.runtime.boundary.context.archive import ConversationChunkStore, inspect_tree
from mas.runtime.boundary.context.archive.persist import spill_cold_payloads
from mas.runtime.boundary.context.assemble import assemble_llm_messages
from mas.runtime.boundary.context.chunk_meta import attach_chunk_meta
from mas.runtime.driver.mocks import AutoCtxAssembler


def _turn(user: str, assistant: str) -> list[dict]:
    return [
        {"role": "user", "content": user},
        {"role": "assistant", "content": assistant},
    ]


def test_fold_keeps_children_and_covers_archive() -> None:
    store = ConversationChunkStore()
    ids = [store.append_turn(_turn(f"q{i}", f"a{i}")) for i in range(4)]
    store.fold(ids[:2], "older")
    assert store.covers_archive()
    projected = store.project_messages()
    assert projected[0]["content"].endswith("older")
    users = [m["content"] for m in projected if m.get("role") == "user"]
    assert users == ["q2", "q3"]
    tree = inspect_tree(store)
    covered = [leaf["chunk_id"] for leaf in tree["leaves"] if leaf["covered_by_summary"]]
    assert set(covered) == set(ids[:2])


def test_nested_fold_still_covers_every_leaf() -> None:
    store = ConversationChunkStore()
    ids = [store.append_turn(_turn(f"q{i}", f"a{i}")) for i in range(6)]
    store.fold(ids[:2], "s1")
    prefix = list(store.order[:2])
    store.fold(prefix, "s2")
    assert store.covers_archive()
    users = [m["content"] for m in store.project_messages() if m.get("role") == "user"]
    assert users == ["q3", "q4", "q5"]
    dump = store.to_dict()
    assert "q0" in str(dump)
    assert "s1" in str(dump)


def test_pinned_skill_leaf_stays_in_covering_view() -> None:
    store = ConversationChunkStore()
    skill = [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "function": {"name": "activate_skill", "arguments": "{}"}}],
        },
        attach_chunk_meta(
            {
                "role": "tool",
                "tool_call_id": "c1",
                "content": '<skill_content name="accord">LOCK RULES</skill_content>',
            },
            {
                "pin_policy": "never",
                "semantic": "instruction",
                "section_id": "skills/activated/accord",
            },
        ),
    ]
    sid = store.append_turn(skill)
    others = [store.append_turn(_turn(f"q{i}", f"a{i}")) for i in range(3)]
    store.fold([sid, others[0]], "older")
    assert store.covers_archive()
    projected = store.project_messages()
    assert "LOCK RULES" in str(projected)
    assert store.chunks[sid].pin_policy == "never"


def test_spill_cold_leaves_then_project_reloads(tmp_path: Path) -> None:
    store = ConversationChunkStore()
    ids = [store.append_turn(_turn("q" + "x" * 200, "a" + "y" * 200)) for _ in range(3)]
    store.fold(ids[:2], "sum")
    spilled = spill_cold_payloads(store, tmp_path, watermark=1)
    assert spilled >= 1
    assert store.chunks[ids[0]].spilled is True
    store.persist_dir = str(tmp_path)
    tree = inspect_tree(store)
    assert "xxx" in str(tree["leaves"])
    assert store.chunks[ids[0]].spilled is True
    assert store.chunks[ids[0]].messages == []
    assert store.project_messages()[0]["content"].endswith("sum")
    assert store.covers_archive()


def test_default_store_does_not_write_disk(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    store = ConversationChunkStore()
    store.append_turn(_turn("hello", "world"))
    assert store.persist_dir == ""
    assert list(tmp_path.rglob("*.json")) == []


def test_assembled_request_is_chat_messages_not_chunk_ids() -> None:
    ctx = AutoCtxAssembler(injected_context=["You are a helper."])
    ctx.note_user_input("hello")
    ctx.note_agent_response("hi there")
    messages = assemble_llm_messages(ctx)
    covering = ctx.conversation_chunks.covering_ids()
    assert covering
    for msg in messages:
        assert "chunk_id" not in msg
        text = str(msg.get("content") or "")
        for cid in covering:
            assert cid not in text
    assert {"role": "user", "content": "hello"} in messages
    assert {"role": "assistant", "content": "hi there"} in messages
    assert ctx.last_context_usage["archive_covering_ids"] == covering


def test_under_limit_assembly_is_verbatim_committed_plus_wm() -> None:
    """Runs that never hit the window keep the same messages[] as a manual join."""
    ctx = AutoCtxAssembler(injected_context=["You are a helper."])
    ctx.note_user_input("hello")
    ctx.note_agent_response("hi there")
    ctx.note_user_input("follow-up")
    ctx.record_assistant_tool_call(call_id="c1", tool_name="lookup", arguments={})
    ctx.record_tool_result(call_id="c1", content="ok")
    first = assemble_llm_messages(ctx)
    second = assemble_llm_messages(ctx)
    assert first == second
    assert not any(str(m.get("content") or "").startswith("[Conversation summary") for m in first)
    assert first[0] == {"role": "system", "content": "You are a helper."}
    assert {"role": "user", "content": "hello"} in first
    assert {"role": "assistant", "content": "hi there"} in first
    users = [m for m in first if m.get("role") == "user"]
    assert users[-1]["content"] == "follow-up"
    assert first[-1]["role"] == "tool" and first[-1]["content"] == "ok"
    assert ctx.conversation_chunks.covers_archive()
    assert ctx.conversation_chunks.project_messages() == ctx.committed_messages


def test_live_skill_body_survives_more_than_twenty_tool_rows() -> None:
    ctx = AutoCtxAssembler(last_user_text="run protocol")
    ctx.record_assistant_tool_call(call_id="sk", tool_name="activate_skill", arguments={})
    ctx.record_tool_result(
        call_id="sk",
        content='<skill_content name="accord">PINNED BODY</skill_content>',
        chunk={
            "pin_policy": "never",
            "semantic": "instruction",
            "section_id": "skills/activated/accord",
        },
    )
    for i in range(24):
        ctx.record_assistant_tool_call(call_id=f"t{i}", tool_name="step", arguments={})
        ctx.record_tool_result(call_id=f"t{i}", content=f"r{i}")
    messages = assemble_llm_messages(ctx)
    assert "PINNED BODY" in str(messages)
    assert len([m for m in messages if m.get("role") == "tool"]) == 25


def test_configure_archive_store_ignores_spec_params() -> None:
    from mas.library.standard.lib.context.spec import configure_archive_store

    ctx = AutoCtxAssembler(archive_dir="/tmp/from-ctx", archive_watermark_bytes=12)
    store = ctx.conversation_chunks
    configure_archive_store(
        store,
        ctx,
        manifest={
            "spec": {
                "context_manager": {
                    "params": {"archive": {"path": "/tmp/from-spec", "memory_watermark_bytes": 99}}
                }
            }
        },
    )
    assert store.persist_dir == "/tmp/from-ctx"
    assert store.memory_watermark_bytes == 12


def test_assemble_annotates_archive_view_id() -> None:
    ctx = AutoCtxAssembler()
    ctx.note_user_input("hello")
    ctx.note_agent_response("world")
    assemble_llm_messages(ctx)
    usage = ctx.last_context_usage or {}
    assert usage["archive_view_id"] == ctx.conversation_chunks.view_id
    assert ctx.conversation_chunks.order[0] in usage["archive_covering_ids"]
    assert usage["system_prefix_hash"] == "" or isinstance(usage["system_prefix_hash"], str)
    assemble_llm_messages(ctx)
    assert ctx.last_context_usage["system_prefix_reused"] is True or ctx.last_context_usage["system_prefix_hash"] == ""


def test_unsafe_chunk_id_does_not_escape_persist_dir(tmp_path: Path) -> None:
    from mas.runtime.boundary.context.archive.chunks import MessageChunk
    from mas.runtime.boundary.context.archive.persist import persist_chunk

    outside = tmp_path / "outside"
    outside.mkdir()
    archive = tmp_path / "archive"
    chunk = MessageChunk(chunk_id="../outside/escaped", messages=[{"role": "user", "content": "x"}])
    assert persist_chunk(chunk, archive) is False
    assert not (outside / "escaped.json").exists()
    assert not (tmp_path / "outside.json").exists()


def test_persist_dir_writes_every_leaf_not_only_overflow(tmp_path: Path) -> None:
    store = ConversationChunkStore()
    store.configure(persist_dir=str(tmp_path), memory_watermark_bytes=10**9)
    cid = store.append_turn(_turn("hello", "world"))
    path = tmp_path / f"{cid}.json"
    assert path.is_file()
    assert "hello" in path.read_text(encoding="utf-8")
    assert store.chunks[cid].messages  # still hot in RAM


def test_shallow_checkpoint_omits_payloads_deep_hydrates(tmp_path: Path) -> None:
    store = ConversationChunkStore()
    store.configure(persist_dir=str(tmp_path), memory_watermark_bytes=1)
    ids = [store.append_turn(_turn("q" + "x" * 80, "a" + "y" * 80)) for _ in range(3)]
    store.fold(ids[:2], "sum")
    shallow = store.checkpoint_payload("shallow")
    deep = store.checkpoint_payload("deep")
    assert shallow["persist_dir"] == str(tmp_path)
    leaf = next(c for c in shallow["chunks"].values() if c.get("kind") != "summary")
    assert leaf["messages"] == []
    hydrated = next(c for c in deep["chunks"].values() if c.get("kind") != "summary")
    assert hydrated["messages"]
    restored = ConversationChunkStore.from_dict(shallow)
    assert restored.project_messages()[0]["content"].endswith("sum")


def test_shallow_without_persist_dir_falls_back_to_deep() -> None:
    store = ConversationChunkStore()
    store.append_turn(_turn("keep", "me"))
    payload = store.checkpoint_payload("reference")
    leaf = next(iter(payload["chunks"].values()))
    assert leaf["messages"][0]["content"] == "keep"
