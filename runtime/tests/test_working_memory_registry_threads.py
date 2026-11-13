#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Sessions on separate threads share no working-memory data.

The registry is process-wide. A session's operations must only touch that
session's own entries, so another session adding or dropping entries on its
own thread can never disturb them.
"""

from __future__ import annotations

import sys
import threading

import pytest
from mas.runtime.boundary.context.working_memory_registry import (
    WorkingMemoryRegistry,
    WorkingMemorySnapshot,
)


@pytest.fixture
def fast_thread_switching():
    """Switch threads very often so an unsafe iteration is hit within a short test."""
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    yield
    sys.setswitchinterval(previous)


def test_one_session_walks_its_entries_while_others_come_and_go(fast_thread_switching):
    registry = WorkingMemoryRegistry()
    errors: list[BaseException] = []

    def churn(worker: int) -> None:
        try:
            for turn in range(1500):
                registry.put(f"churn-{worker}-{turn}", "agent", WorkingMemorySnapshot())
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def own_session(worker: int) -> None:
        session = f"own-{worker}"
        try:
            for _ in range(1500):
                registry.put(session, "agent", WorkingMemorySnapshot())
                registry.freeze_session(session)
                registry.wm_version(session)
                registry.export_session(session)
                registry.clone_for_fork(session, f"{session}-fork")
                registry.clear_session(session)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=churn, args=(i,)) for i in range(4)]
    threads += [threading.Thread(target=own_session, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []


def test_clearing_one_session_leaves_the_others_alone():
    registry = WorkingMemoryRegistry()
    registry.put("a", "x", WorkingMemorySnapshot(open_tool_call_id="a-x"))
    registry.put("b", "x", WorkingMemorySnapshot(open_tool_call_id="b-x"))
    registry.clear_session("a")
    assert registry.get("a", "x") is None
    assert registry.get("b", "x").open_tool_call_id == "b-x"
    registry.drop("b", "x")
    assert registry.get("b", "x") is None
