#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
from __future__ import annotations

from mas.library.standard.lib.triggers import TriggerMatcher, compile_trigger


def _trigger(**kwargs):
    payload = {"id": "t", "when": {"on": "tool_result", "tool": "pay_fare"}, **kwargs}
    payload["when"] = {**{"on": "tool_result", "tool": "pay_fare"}, **kwargs.get("when", payload["when"])}
    if "when" in kwargs:
        payload["when"] = kwargs["when"]
    return compile_trigger(payload)


def test_occurrence_per_run_and_max_fires() -> None:
    trigger = compile_trigger(
        {
            "id": "once",
            "when": {"on": "tool_result", "tool": "pay_fare", "occurrence": 2},
        }
    )
    matcher = TriggerMatcher((trigger,))
    assert matcher.match(on="tool_result", tool="pay_fare") == ()
    hits = matcher.match(on="tool_result", tool="pay_fare")
    assert [item.id for item in hits] == ["once"]
    assert matcher.match(on="tool_result", tool="pay_fare") == ()


def test_every_and_max_fires() -> None:
    trigger = compile_trigger(
        {
            "id": "every",
            "when": {"on": "tool_call", "occurrence": "every"},
            "max_fires": 2,
        }
    )
    matcher = TriggerMatcher((trigger,))
    assert len(matcher.match(on="tool_call", tool="a")) == 1
    assert len(matcher.match(on="tool_call", tool="b")) == 1
    assert matcher.match(on="tool_call", tool="c") == ()


def test_tool_and_agent_filters() -> None:
    trigger = compile_trigger(
        {
            "id": "payer",
            "when": {"on": "tool_result", "tool": "pay_fare", "agent": "payer", "occurrence": "every"},
        }
    )
    matcher = TriggerMatcher((trigger,))
    assert matcher.match(on="tool_result", tool="other", agent="payer") == ()
    assert matcher.match(on="tool_result", tool="pay_fare", agent="other") == ()
    assert [item.id for item in matcher.match(on="tool_result", tool="pay_fare", agent="payer")] == ["payer"]


def test_declaration_order() -> None:
    a = compile_trigger({"id": "a", "when": {"on": "tool_call", "occurrence": "every"}})
    b = compile_trigger({"id": "b", "when": {"on": "tool_call", "occurrence": "every"}})
    matcher = TriggerMatcher((a, b))
    hits = matcher.match(on="tool_call", tool="x")
    assert [item.id for item in hits] == ["a", "b"]


def test_entry_agent_closed_unless_resolved() -> None:
    trigger = compile_trigger({"id": "entry", "when": {"on": "tool_result", "agent": "$entry"}})
    matcher = TriggerMatcher((trigger,))
    assert matcher.match(on="tool_result", tool="pay_fare", agent="payer") == ()
    hits = matcher.match(on="tool_result", tool="pay_fare", agent="payer", entry_agent="payer")
    assert [item.id for item in hits] == ["entry"]
    assert matcher.match(on="tool_result", tool="pay_fare", agent="other", entry_agent="payer") == ()


def test_non_repeat_never_fires_twice_small_bound() -> None:
    tools = ("a", "b", "c")
    agents = ("x", "y")
    trigger = compile_trigger({"id": "once", "when": {"on": "tool_result", "tool": "a", "agent": "x"}})
    sequences = [[]]
    for _ in range(6):
        sequences = [seq + [(t, g)] for seq in sequences for t in tools for g in agents]
        if len(sequences) > 200:
            sequences = sequences[:200]
    for seq in sequences:
        matcher = TriggerMatcher((trigger,))
        fired = 0
        for tool, agent in seq:
            hits = matcher.match(on="tool_result", tool=tool, agent=agent)
            fired += len(hits)
        assert fired <= 1
