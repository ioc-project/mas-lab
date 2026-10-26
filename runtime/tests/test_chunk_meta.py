#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from mas.runtime.boundary.context.archive import ConversationChunkStore
from mas.runtime.boundary.context.chunk_meta import (
    attach_chunk_meta,
    pop_tool_chunk,
    strip_chunk_meta,
    strongest_pin_policy,
)
from mas.runtime.engine.tool_dispatch import format_tool_result


def test_markup_without_metadata_is_not_pinned() -> None:
    store = ConversationChunkStore()
    cid = store.append_turn(
        [
            {"role": "user", "content": "go"},
            {
                "role": "tool",
                "tool_call_id": "c1",
                "content": '<skill_content name="accord">LOCK RULES</skill_content>',
            },
        ]
    )
    assert store.chunks[cid].pin_policy == "ephemeral"
    store.fold([cid], "older")
    assert cid not in store.covering_ids()


def test_project_tool_result_attaches_pending_chunk() -> None:
    from mas.runtime.boundary.context.working_memory import WorkingMemoryStore

    ctx = type("Ctx", (), {})()
    format_tool_result(
        {"content": "SKILL BODY", "chunk": {"pin_policy": "never", "semantic": "instruction"}},
        ctx=ctx,
        call_id="call_1",
    )
    store = WorkingMemoryStore()
    store.record_assistant_tool_call(call_id="call_1", tool_name="activate_skill", arguments={})
    bound = store.project_tool_result(cid=7, content="SKILL BODY", ctx=ctx)
    assert bound == "call_1"
    tool = store.messages[-1]
    assert tool["content"] == "SKILL BODY"
    assert tool["mas"]["pin_policy"] == "never"


def test_activate_skill_chunk_is_stripped_from_model_json() -> None:
    ctx = type("Ctx", (), {})()
    text = format_tool_result(
        {
            "content": "SKILL BODY",
            "skill": "fmt",
            "chunk": {
                "pin_policy": "never",
                "semantic": "instruction",
                "section_id": "skills/activated/fmt",
            },
        },
        ctx=ctx,
        call_id="c1",
    )
    assert "SKILL BODY" in text
    assert "pin_policy" not in text
    assert '"chunk"' not in text
    from mas.runtime.boundary.context.chunk_meta import take_pending_chunk_meta

    meta = take_pending_chunk_meta(ctx, "c1")
    assert meta["pin_policy"] == "never"
    assert meta["section_id"] == "skills/activated/fmt"


def test_provider_strip_drops_mas_key() -> None:
    msg = attach_chunk_meta({"role": "tool", "content": "x"}, {"pin_policy": "never"})
    assert "mas" in msg
    stripped = strip_chunk_meta([msg])
    assert "mas" not in stripped[0]
    assert stripped[0]["content"] == "x"


def test_pop_tool_chunk_leaves_payload() -> None:
    payload, meta = pop_tool_chunk({"content": "body", "chunk": {"pin_policy": "never"}})
    assert payload == {"content": "body"}
    assert meta["pin_policy"] == "never"


def test_strongest_pin_is_never() -> None:
    msgs = [
        {"role": "user", "content": "q"},
        attach_chunk_meta({"role": "tool", "content": "b"}, {"pin_policy": "never"}),
    ]
    assert strongest_pin_policy(msgs) == "never"
