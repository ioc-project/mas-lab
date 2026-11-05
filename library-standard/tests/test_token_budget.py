#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``trim_messages_to_budget`` — including the pinned-tail budget backstop.

Before issue #65's fix, ``pin_tail`` (the in-turn working memory) was exempt
from budget trimming outright: if it alone exceeded the budget the code just
logged a warning and returned it whole. That meant a long stuck retry loop
with ``context_manager.params.trimmer`` could still blow the budget forever.
"""

from __future__ import annotations

from mas.library.standard.plugins.context.provider_payload import assert_provider_payload
from mas.library.standard.plugins.context.token_budget import estimate_tokens, trim_messages_to_budget


def _tool_group(call_id: str, arg_len: int = 20) -> list[dict]:
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": call_id, "function": {"name": "get_metrics", "arguments": "x" * arg_len}}],
        },
        {"role": "tool", "tool_call_id": call_id, "content": "y" * arg_len},
    ]


def test_pin_tail_alone_over_budget_is_trimmed_not_just_warned() -> None:
    pin_tail = _tool_group("c1") + _tool_group("c2") + _tool_group("c3") + _tool_group("c4")
    budget_for_one_group = estimate_tokens(_tool_group("cN")) + 4  # just over one group's cost
    out = trim_messages_to_budget([], max_tokens=budget_for_one_group, pin_tail=pin_tail)
    assert len(out) < len(pin_tail), "oldest pinned tool-call groups must be dropped, not kept whole"
    assert out[-1]["tool_call_id"] == "c4", "most recent group must survive"
    assert_provider_payload(out)


def test_pin_tail_keeps_at_least_the_most_recent_group_even_if_over_budget() -> None:
    pin_tail = _tool_group("only", arg_len=5000)
    out = trim_messages_to_budget([], max_tokens=10, pin_tail=pin_tail)
    assert out == pin_tail, "the single most-recent group must never be dropped, even over budget"


def test_pin_tail_under_budget_is_untouched() -> None:
    pin_tail = _tool_group("c1")
    out = trim_messages_to_budget([{"role": "system", "content": "sys"}], max_tokens=10_000, pin_tail=pin_tail)
    assert out[-2:] == pin_tail


def test_committed_history_trimmed_before_pinned_tail() -> None:
    committed = [{"role": "user", "content": f"turn {i}"} for i in range(50)]
    pin_tail = _tool_group("latest")
    budget = estimate_tokens(pin_tail) + estimate_tokens(committed[-2:]) + 8
    out = trim_messages_to_budget(committed, max_tokens=budget, pin_tail=pin_tail)
    assert out[-2:] == pin_tail
    assert len(out) < len(committed) + len(pin_tail)


def test_history_tool_groups_trimmed_atomically() -> None:
    history: list[dict] = []
    for i in range(8):
        history.extend(_tool_group(f"call_{i}", arg_len=400))
    budget = estimate_tokens(_tool_group("call_7", arg_len=400)) + 40
    out = trim_messages_to_budget(history, max_tokens=budget)
    assert_provider_payload(out)
    assert out[-1]["tool_call_id"] == "call_7"


def test_assistant_in_history_tool_results_only_in_pin_tail() -> None:
    """Dropping the committed assistant must not leave orphan tools in pin_tail."""
    history = [{"role": "user", "content": "q"}] + _tool_group("committed", arg_len=30)[:1]
    pin_tail = _tool_group("committed", arg_len=30)[1:] + _tool_group("live", arg_len=30)
    budget = estimate_tokens(pin_tail) + 20
    out = trim_messages_to_budget(history, max_tokens=budget, pin_tail=pin_tail)
    assert_provider_payload(out)
    assert out[-2:] == pin_tail[-2:]
