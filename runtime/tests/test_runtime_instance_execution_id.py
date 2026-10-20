#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Every execution of an agent gets its own call id, even when the turn id repeats."""

from __future__ import annotations

from typing import Any

from mas.runtime.driver.instance import RuntimeInstance


def _instance() -> RuntimeInstance:
    return RuntimeInstance(kernel=None, driver=None)  # type: ignore[arg-type]


def test_first_execution_keeps_the_plain_id() -> None:
    assert _instance()._execution_id("planner", "u1") == "planner-u1-exec"


def test_repeated_turn_id_gets_a_distinct_id_per_execution() -> None:
    inst = _instance()
    ids = [inst._execution_id("a2a-agent", "msg-1") for _ in range(3)]
    assert ids == ["a2a-agent-msg-1-exec", "a2a-agent-msg-1-2-exec", "a2a-agent-msg-1-3-exec"]
    assert all(i.endswith("-exec") for i in ids)


def test_ids_are_counted_per_agent_and_turn() -> None:
    inst = _instance()
    first: Any = inst._execution_id("a", "u1")
    other_agent = inst._execution_id("b", "u1")
    other_turn = inst._execution_id("a", "u2")
    assert len({first, other_agent, other_turn}) == 3
    assert not any("-2-" in i for i in (first, other_agent, other_turn))
