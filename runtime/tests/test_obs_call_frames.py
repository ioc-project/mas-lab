#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Call-frame isolation across concurrent tasks and threads."""

import asyncio
import threading

import pytest
from mas.runtime.boundary.obs.operator import ObservabilityOperator


@pytest.mark.asyncio
async def test_call_frames_are_isolated_between_concurrent_tasks():
    """Two interleaved turns must not see each other's frames."""
    operator = ObservabilityOperator()
    observed: dict[str, tuple[str, ...]] = {}

    async def turn(name: str) -> None:
        operator.push_call_frame(f"{name}-exec")
        await asyncio.sleep(0)  # force the other task to interleave here
        observed[name] = operator._frames.stack
        operator.pop_call_frame(f"{name}-exec")

    await asyncio.gather(turn("a"), turn("b"))

    assert observed["a"] == ("a-exec",)
    assert observed["b"] == ("b-exec",)
    assert operator._frames.stack == ()


@pytest.mark.asyncio
async def test_child_task_inherits_but_does_not_mutate_parent_frames():
    operator = ObservabilityOperator()
    operator.push_call_frame("parent-exec")
    child_view: list[tuple[str, ...]] = []

    async def child() -> None:
        child_view.append(operator._frames.stack)
        operator.push_call_frame("child-exec")

    await asyncio.gather(child())

    assert child_view == [("parent-exec",)]
    assert operator._frames.stack == ("parent-exec",)


def test_call_frames_are_isolated_between_threads():
    operator = ObservabilityOperator()
    operator.push_call_frame("main-exec")
    observed: list[tuple[str, ...]] = []

    def worker() -> None:
        operator.push_call_frame("worker-exec")
        observed.append(operator._frames.stack)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert observed == [("worker-exec",)]
    assert operator._frames.stack == ("main-exec",)


def test_two_operators_keep_separate_stacks():
    first = ObservabilityOperator()
    second = ObservabilityOperator()

    first.push_call_frame("first-exec")

    assert first._frames.stack == ("first-exec",)
    assert second._frames.stack == ()
