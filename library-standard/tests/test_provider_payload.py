#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Provider payload repair for committed history after trimming (PR #55 regressions)."""

from __future__ import annotations

from typing import Any

from mas.library.standard.plugins.context.assembler import ContextAssemblerPlugin
from mas.library.standard.plugins.context.conversation import (
    SlidingWindowConversation,
    StackConversation,
    SummarizingConversation,
)
from mas.library.standard.plugins.context.provider_payload import (
    assert_provider_payload,
    sanitize_provider_messages,
    tool_call_pairs,
)
from mas.library.standard.plugins.context.token_budget import trim_messages_to_budget
from mas.runtime.aio import settle


def _tool_turn(call_id: str = "call_1", *, content: str = "result") -> list[dict[str, Any]]:
    return [
        {"role": "user", "content": "ask"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": call_id, "function": {"name": "f", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": call_id, "content": content},
        {"role": "assistant", "content": "done"},
    ]


def test_sanitize_drops_orphan_tool_message() -> None:
    out = sanitize_provider_messages(
        [
            {"role": "tool", "tool_call_id": "missing", "content": "orphan"},
            {"role": "user", "content": "hi"},
        ]
    )
    assert out == [{"role": "user", "content": "hi"}]


def test_sanitize_strips_incomplete_assistant_tool_calls() -> None:
    out = sanitize_provider_messages(
        [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_1", "function": {"name": "f", "arguments": "{}"}}],
            },
            {"role": "user", "content": "follow up"},
        ]
    )
    assert out == [
        {"role": "user", "content": "hi"},
        {"role": "user", "content": "follow up"},
    ]


def test_sanitize_preserves_complete_tool_exchange() -> None:
    out = sanitize_provider_messages(_tool_turn())
    assert_provider_payload(out)


def test_sanitize_drops_duplicate_tool_results_in_a_group() -> None:
    """Two calls, three result rows: keep one each."""
    out = sanitize_provider_messages(
        [
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call_a", "function": {"name": "f", "arguments": "{}"}},
                    {"id": "call_b", "function": {"name": "g", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "call_a", "content": "ra"},
            {"role": "tool", "tool_call_id": "call_b", "content": "rb"},
            {"role": "tool", "tool_call_id": "call_a", "content": "ra-dup"},
        ]
    )
    tool_msgs = [m for m in out if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["call_a", "call_b"]
    assert_provider_payload(out)


def test_sanitize_does_not_glue_incomplete_parallel_onto_previous_turn() -> None:
    """Incomplete 2-call group at the end: fill the missing id, do not glue
    the leftover tool row onto the previous turn (that was the extra-results 400).
    """
    out = sanitize_provider_messages(
        [
            {"role": "user", "content": "q1"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_1", "function": {"name": "f", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "r1"},
            {"role": "assistant", "content": "done1"},
            {"role": "user", "content": "q2"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call_2", "function": {"name": "f", "arguments": "{}"}},
                    {"id": "call_3", "function": {"name": "g", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "call_2", "content": "r2"},
        ]
    )
    tools = [m for m in out if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in tools] == ["call_1", "call_2", "call_3"]
    assert tools[-1]["content"] == ""  # filled for the missing call_3
    assert_provider_payload(out)


def test_sanitize_rebinds_hitl_steering_tool_to_preceding_assistant() -> None:
    out = sanitize_provider_messages(
        [
            {"role": "user", "content": "Who is POTUS?"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_1", "function": {"name": "search", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "call_3", "content": "Maxence Postu is POTUS"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_4", "function": {"name": "search", "arguments": "{}"}}],
            },
            {"role": "user", "content": "Follow-up?"},
        ]
    )
    tool_msgs = [m for m in out if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "call_1"
    assert_provider_payload(out)


async def test_stack_trim_plus_sanitize_is_provider_safe() -> None:
    past = _tool_turn("call_0") + _tool_turn("call_1")
    trimmed = await settle(StackConversation(max_messages=3).manage_history(past, budget_tokens=0))
    out = sanitize_provider_messages(trimmed)
    assert_provider_payload(out)


async def test_sliding_window_keeps_whole_user_turn_with_parallel_tools() -> None:
    """max_turns=1 must keep both results of a parallel call, not only the last row."""
    past = _tool_turn("old") + [
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "a", "function": {"name": "f", "arguments": "{}"}},
                {"id": "b", "function": {"name": "g", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "a", "content": "ra"},
        {"role": "tool", "tool_call_id": "b", "content": "rb"},
        {"role": "assistant", "content": "done"},
    ]
    out = await settle(SlidingWindowConversation(max_turns=1).manage_history(past, budget_tokens=0))
    assert [m.get("tool_call_id") for m in out if m.get("role") == "tool"] == ["a", "b"]
    assert_provider_payload(out)


async def test_sliding_window_plus_sanitize_is_provider_safe() -> None:
    past: list[dict[str, Any]] = []
    for i in range(6):
        past.extend(_tool_turn(f"call_{i}"))
    trimmed = await settle(SlidingWindowConversation(max_turns=1).manage_history(past, budget_tokens=0))
    out = sanitize_provider_messages(trimmed)
    assert_provider_payload(out)


async def test_summarizing_conversation_plus_sanitize_is_provider_safe() -> None:
    past: list[dict[str, Any]] = []
    for i in range(6):
        past.extend(_tool_turn(f"call_{i}"))
    cm = SummarizingConversation(
        summary_threshold=1,
        keep_turns=1,
        summarize_fn=lambda msgs: "summary of earlier turns",
    )
    trimmed = await settle(cm.manage_history(past, budget_tokens=0))
    out = sanitize_provider_messages(trimmed)
    assert_provider_payload(out)
    assert trimmed[-4:] == past[-4:]
    assert trimmed[0]["role"] == "system"


async def test_summarizing_keeps_last_round_verbatim_without_summarize_fn() -> None:
    past: list[dict[str, Any]] = []
    for i in range(4):
        past.extend(_tool_turn(f"call_{i}", content=f"result-{i}"))
    last_round = past[-4:]
    cm = SummarizingConversation(summary_threshold=1, keep_turns=1)
    out = await settle(cm.manage_history(past, budget_tokens=0))
    assert out == last_round
    assert_provider_payload(out)


async def test_summarizing_under_budget_does_not_touch_history() -> None:
    past = _tool_turn("call_old") + _tool_turn("call_new")
    cm = SummarizingConversation(
        summary_threshold=1,
        keep_turns=1,
        summarize_fn=lambda msgs: "should not run",
    )
    assert await settle(cm.manage_history(past, budget_tokens=10_000)) == past


async def test_sliding_window_accepts_keep_turns_alias() -> None:
    past: list[dict[str, Any]] = []
    for i in range(4):
        past.extend(_tool_turn(f"call_{i}"))
    out = await settle(SlidingWindowConversation(keep_turns=1).manage_history(past, budget_tokens=0))
    assert out == past[-4:]
    assert_provider_payload(out)


async def test_summarizing_hysteresis_reuses_summary_on_the_next_call() -> None:
    """Committed history is still the full list; without a cache we would
    re-summarize the same older turns on every LLM call."""
    calls: list[int] = []

    def summarize(msgs: list[dict[str, Any]]) -> str:
        calls.append(len(msgs))
        return "SUM"

    def fat(i: int) -> list[dict[str, Any]]:
        return _tool_turn(f"call_{i}", content="x" * 800)

    past = fat(0) + fat(1) + fat(2)
    high = SummarizingConversation._estimate_tokens(past) - 1
    cm = SummarizingConversation(keep_turns=1, hysteresis_ratio=0.2, summarize_fn=summarize)
    first = await settle(cm.manage_history(past, high))
    assert len(calls) == 1
    assert first[0]["role"] == "system"
    assert await settle(cm.manage_history(past, high)) == first
    assert len(calls) == 1
    grown = past + fat(3)
    reused = await settle(cm.manage_history(grown, high))
    assert len(calls) == 1
    assert reused[0]["content"] == first[0]["content"]
    assert_provider_payload(reused)


async def test_summarizing_hysteresis_does_not_reuse_a_different_prefix() -> None:
    """Same compressed-turn count, different conversation — do not attach
    the previous summary. This is the WM-restore / context_id case."""
    calls: list[str] = []

    def summarize(msgs: list[dict[str, Any]]) -> str:
        calls.append(str(msgs[0].get("content", "")))
        return f"SUM-{len(calls)}"

    def fat(label: str) -> list[dict[str, Any]]:
        return _tool_turn(label, content=label + "-x" * 400)

    cm = SummarizingConversation(keep_turns=1, hysteresis_ratio=0.2, summarize_fn=summarize)
    first_past = fat("alpha") + fat("beta") + fat("gamma")
    high = SummarizingConversation._estimate_tokens(first_past) - 1
    first = await settle(cm.manage_history(first_past, high))
    assert len(calls) == 1
    other_past = fat("other-a") + fat("other-b") + fat("other-c")
    second = await settle(cm.manage_history(other_past, high))
    assert len(calls) == 2
    assert first[0]["content"] != second[0]["content"]
    assert "SUM-2" in second[0]["content"]


async def test_summarizing_recompacts_after_hysteresis_ceiling() -> None:
    calls: list[int] = []

    def summarize(msgs: list[dict[str, Any]]) -> str:
        calls.append(len(msgs))
        return f"SUM-{len(calls)}"

    def fat(i: int) -> list[dict[str, Any]]:
        return _tool_turn(f"call_{i}", content="x" * 800)

    past = fat(0) + fat(1) + fat(2)
    high = SummarizingConversation._estimate_tokens(past) - 1
    cm = SummarizingConversation(keep_turns=1, hysteresis_ratio=0.0, summarize_fn=summarize)
    await settle(cm.manage_history(past, high))
    assert len(calls) == 1
    for i in range(3, 12):
        past = past + fat(i)
        await settle(cm.manage_history(past, high))
        if len(calls) == 2:
            break
    assert len(calls) == 2


def test_trim_with_structural_pin_tail() -> None:
    history = [{"role": "user", "content": "x" * 5000} for _ in range(8)]
    wm = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call_wm", "function": {"name": "f", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call_wm", "content": "live"},
    ]
    out = trim_messages_to_budget(history, max_tokens=400, pin_tail=wm)
    assert out[-2:] == wm
    assert_provider_payload(out)


# --- slice-boundary pairing (orphan tool at the cut) ---


async def test_pr55_stack_slice_orphan_tool_at_front() -> None:
    """Stack trim used to slice mid-group; manage_history must return a valid payload."""
    past = _tool_turn("call_0") + _tool_turn("call_1")
    trimmed = await settle(StackConversation(max_messages=2).manage_history(past, budget_tokens=0))
    assert_provider_payload(trimmed)


async def test_pr55_stack_various_max_messages_never_violate_invariant() -> None:
    past: list[dict[str, Any]] = []
    for i in range(10):
        past.extend(_tool_turn(f"call_{i}"))
    for max_msgs in range(1, 20):
        trimmed = await settle(StackConversation(max_messages=max_msgs).manage_history(past, budget_tokens=0))
        declared, returned = tool_call_pairs(trimmed)
        assert returned <= declared
        assert_provider_payload(trimmed)


async def test_pr55_sliding_window_split_exchange() -> None:
    past: list[dict[str, Any]] = []
    for i in range(8):
        past.extend(_tool_turn(f"call_{i}"))
    trimmed = await settle(SlidingWindowConversation(max_turns=2).manage_history(past, budget_tokens=0))
    out = sanitize_provider_messages(trimmed)
    assert_provider_payload(out)


def test_pr55_token_budget_many_sizes_on_tool_history() -> None:
    past: list[dict[str, Any]] = []
    for i in range(6):
        past.extend(_tool_turn(f"call_{i}", content=f"payload-{i}-" + "x" * 200))
    for budget in (50, 100, 200, 400, 800, 1600, 3200):
        out = trim_messages_to_budget(past, max_tokens=budget)
        out = sanitize_provider_messages(out)
        assert_provider_payload(out)


async def test_pr55_assembler_end_to_end_with_live_tool_exchange() -> None:
    plugin = ContextAssemblerPlugin(conversation_strategy=StackConversation(max_messages=6))
    history = _tool_turn("call_0") + _tool_turn("call_1") + _tool_turn("call_2")
    history.append({"role": "user", "content": "current question"})
    history.append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call_live", "function": {"name": "f", "arguments": "{}"}}],
        }
    )
    out = await plugin._apply_conversation_strategy(history)
    declared, returned = tool_call_pairs(out)
    assert "call_live" in declared
    assert returned <= declared
    assert_provider_payload(out)


def test_sanitize_keeps_provider_state_and_error_flag_and_drops_with_stripped_call() -> None:
    state = {"blocks": [{"signature": "s"}]}
    kept = [
        {"role": "user", "content": "ask"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "function": {"name": "f", "arguments": "{}"}}],
            "provider_state": state,
        },
        {"role": "tool", "tool_call_id": "c1", "content": "bad", "is_error": True},
    ]
    out = sanitize_provider_messages(kept)
    assert out[1]["provider_state"] == state
    assert out[2]["is_error"] is True
    orphan = [
        {
            "role": "assistant",
            "content": "thinking aloud",
            "tool_calls": kept[1]["tool_calls"],
            "provider_state": state,
        },
        {"role": "user", "content": "next"},
    ]
    stripped = sanitize_provider_messages(orphan)
    assert "provider_state" not in stripped[0] and "tool_calls" not in stripped[0]


def test_token_estimate_ignores_provider_state() -> None:
    from mas.library.standard.plugins.context.token_budget import estimate_tokens

    base = {"role": "assistant", "content": "x", "tool_calls": []}
    assert estimate_tokens([{**base, "provider_state": {"blob": "y" * 4000}}]) == estimate_tokens([base])
