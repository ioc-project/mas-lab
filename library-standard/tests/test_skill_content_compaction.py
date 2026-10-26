#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Activated skill content is never summarized or dropped (agentskills.io Step 5)."""

import json

from mas.library.standard.lib.context.pinned_history import (
    drop_prefix_duplicates,
    is_retained_instruction,
    split_pinned_prefix,
)
from mas.library.standard.lib.context.working_memory import bounded_working_memory_tail
from mas.library.standard.plugins.context.conversation import (
    SlidingWindowConversation,
    StackConversation,
    SummarizingConversation,
)
from mas.library.standard.plugins.context.token_budget import trim_messages_to_budget
from mas.runtime.boundary.context.chunk_meta import attach_chunk_meta

BODY = "Always end with a one-sentence summary."


def _skill_result(name: str = "fmt", body: str = BODY) -> str:
    content = f'<skill_content name="{name}">\n{body}\n\nSkill directory: /s/{name}\n</skill_content>'
    return json.dumps({"content": content, "skill": name}, indent=2)


def _activation_turn(call_id: str = "c1", name: str = "fmt", body: str = BODY) -> list[dict]:
    return [
        {"role": "user", "content": "q0"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": call_id, "type": "function", "function": {"name": "activate_skill", "arguments": "{}"}}
            ],
        },
        attach_chunk_meta(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": _skill_result(name, body),
            },
            {
                "pin_policy": "never",
                "semantic": "instruction",
                "section_id": f"skills/activated/{name}",
                "source": "activate_skill",
            },
        ),
        {"role": "assistant", "content": "a0"},
    ]


def _history(n_turns: int = 6) -> list[dict]:
    past = _activation_turn()
    for i in range(1, n_turns):
        past += [
            {"role": "user", "content": f"q{i} " + "x" * 400},
            {"role": "assistant", "content": f"a{i} " + "y" * 400},
        ]
    return past


def _skill_rows(messages: list[dict]) -> list[dict]:
    return [m for m in messages if is_retained_instruction(m) and m.get("role") == "system"]


def test_summarizer_never_sees_skill_body_and_body_is_retained():
    seen: list[list[dict]] = []
    cm = SummarizingConversation(summary_threshold=100, keep_turns=2)
    cm.bind_summarizer(lambda msgs: (seen.append(msgs), "SUMMARY")[1])

    managed = cm.manage_history(_history(), 0)

    assert seen and BODY not in json.dumps(seen)
    assert "[content retained verbatim: skills/activated/fmt]" in json.dumps(seen)
    assert managed[0]["content"].endswith("SUMMARY")
    rows = _skill_rows(managed)
    assert len(rows) == 1 and BODY in rows[0]["content"]


def test_cached_summary_view_keeps_skill_rows():
    cm = SummarizingConversation(summary_threshold=600, keep_turns=2, hysteresis_ratio=1.0)
    cm.bind_summarizer(lambda msgs: "SUMMARY")
    past = _history()
    cm.manage_history(past, 0)
    past += [{"role": "user", "content": "q-next"}, {"role": "assistant", "content": "a-next"}]

    managed = cm.manage_history(past, 0)

    assert cm.last_compaction_metadata["reused"] is True
    assert len(_skill_rows(managed)) == 1


def test_drop_summarizer_keeps_skill_body():
    cm = SummarizingConversation(summary_threshold=100, keep_turns=2)
    cm.bind_summarizer(None)

    managed = cm.manage_history(_history(), 0)

    assert len(_skill_rows(managed)) == 1
    assert not any(m.get("role") == "tool" for m in managed)


def test_repeated_compaction_keeps_one_row_per_skill_latest_wins():
    cm = SummarizingConversation(summary_threshold=100, keep_turns=2)
    cm.bind_summarizer(lambda msgs: "SUMMARY")
    past = _history() + _activation_turn("c2", body="Updated body.") + _history()[4:]

    once = cm.manage_history(past, 0)
    twice = cm.manage_history(once + _history()[4:], 0)

    rows = _skill_rows(twice)
    assert len(rows) == 1
    assert "Updated body." in rows[0]["content"]


def test_sliding_window_keeps_skill_body():
    managed = SlidingWindowConversation(keep_turns=2).manage_history(_history(), 0)
    assert len(_skill_rows(managed)) == 1


def test_stack_cap_keeps_skill_body():
    managed = StackConversation(max_messages=4).manage_history(_history(), 0)
    assert len(_skill_rows(managed)) == 1


def test_split_ignores_user_text_that_quotes_the_tag():
    msg = {"role": "user", "content": '<skill_content name="x">hi</skill_content>'}
    rest, retained = split_pinned_prefix([msg])
    assert rest == [msg] and retained == []


def test_trimmer_never_drops_retained_skill_rows():
    _, retained = split_pinned_prefix(_activation_turn())
    messages = [{"role": "system", "content": "sys"}, *retained, *_history()[4:]]

    trimmed = trim_messages_to_budget(messages, max_tokens=150)

    assert len(_skill_rows(trimmed)) == 1
    assert len(trimmed) < len(messages)


def test_working_memory_tail_keeps_skill_tool_group():
    """Count-slice (if a caller still passes a limit) must not drop activate_skill."""
    history = _activation_turn()[1:]  # assistant + tool (no user)
    for i in range(6):
        history += [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": f"x{i}", "function": {"name": "t", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": f"x{i}", "content": f"r{i}"},
        ]
    kept = bounded_working_memory_tail(history, 4)
    assert BODY in str(kept)
    assert kept[-1]["tool_call_id"] == "x5"


def test_pinned_skill_row_is_not_duplicated():
    _, retained = split_pinned_prefix(_activation_turn())
    history = [*retained, {"role": "user", "content": "q"}]

    pinned = drop_prefix_duplicates(history, {"skills/activated/fmt"})
    other = drop_prefix_duplicates(history, {"skills/activated/other"})
    unchanged = drop_prefix_duplicates(history, set())

    assert _skill_rows(pinned) == []
    assert len(_skill_rows(other)) == 1
    assert unchanged == history


def test_never_pin_tool_result_is_not_a_retained_instruction():
    msg = attach_chunk_meta(
        {"role": "tool", "tool_call_id": "c", "content": "payload"},
        {"pin_policy": "never", "semantic": "tool_result", "section_id": "tools/x"},
    )
    assert is_retained_instruction(msg) is False
