#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import asyncio

import pytest
from mas.lab.benchmark.pipeline.adapter import LegacyStepAdapter
from mas.lab.benchmark.pipeline.keys import RunKey
from mas.lab.benchmark.pipeline.models import StepOutput
from mas.lab.benchmark.pipeline.scheduler import Node, ResourcePool, Scheduler


class _BatchEval:
    type = "eval_mce"
    name = "eval"
    config: dict = {}
    realtime = False
    mode = "batch"
    ran = 0

    async def execute(self, ctx):  # noqa: ANN001
        del ctx
        type(self).ran += 1
        self.ran += 1
        return StepOutput(data={"ok": True})


class _LiveFold:
    type = "extract_trace_stats"
    name = "stats"
    config: dict = {}
    realtime = True
    mode = "live"
    ran = 0
    flagged: list[bool] = []

    def on_batch(self, batch, ctx):  # noqa: ANN001
        del batch, ctx
        self.ran += 1

    async def execute(self, ctx):  # noqa: ANN001
        from mas.lab.benchmark.execution.completeness import current_run_complete

        type(self).flagged.append(current_run_complete())
        self.ran += 1
        del ctx
        return StepOutput(data={"ok": True})


@pytest.mark.asyncio
async def test_batch_stage_skips_failed_run() -> None:
    _BatchEval.ran = 0
    step = _BatchEval()
    node = Node(
        name="eval",
        scope="run",
        phase="post",
        step=step,
        adapter=LegacyStepAdapter(step),
        key=RunKey("e", "s", "item1", "r1"),
    )
    sched = Scheduler([node], resources=ResourcePool())
    await sched.mark_run_terminal(node.key, "failed")
    await asyncio.wait_for(sched.drain(), timeout=2)
    assert node.status == "skipped"
    assert step.ran == 0


@pytest.mark.asyncio
async def test_live_stage_receives_failed_flagged_incomplete() -> None:
    _LiveFold.flagged = []
    step = _LiveFold()
    node = Node(
        name="stats",
        scope="run",
        phase="post",
        step=step,
        adapter=LegacyStepAdapter(step),
        key=RunKey("e", "s", "item1", "r1"),
    )
    sched = Scheduler([node], resources=ResourcePool())
    await sched.mark_run_terminal(node.key, "failed")
    await asyncio.wait_for(sched.drain(), timeout=2)
    assert node.status == "ok"
    assert _LiveFold.flagged == [False]


@pytest.mark.asyncio
async def test_steps_downstream_of_a_skipped_batch_step_do_not_run_on_nothing() -> None:
    class _Later(_BatchEval):
        name = "later"
        ran = 0

    class _Last(_BatchEval):
        name = "last"
        ran = 0

    first, later, last = _BatchEval(), _Later(), _Last()
    _BatchEval.ran = 0
    key = RunKey("e", "s", "item1", "r1")
    nodes = [
        Node(name="eval", scope="run", phase="post", step=first, adapter=LegacyStepAdapter(first), key=key),
        Node(
            name="later",
            scope="run",
            phase="post",
            step=later,
            adapter=LegacyStepAdapter(later),
            key=key,
            depends_on=["eval"],
        ),
        Node(
            name="last",
            scope="run",
            phase="post",
            step=last,
            adapter=LegacyStepAdapter(last),
            key=key,
            depends_on=["later"],
        ),
    ]
    sched = Scheduler(nodes, resources=ResourcePool())
    await sched.mark_run_terminal(key, "failed")
    await asyncio.wait_for(sched.drain(), timeout=2)
    assert [n.status for n in nodes] == ["skipped", "skipped", "skipped"]
    assert (first.ran, later.ran, last.ran) == (0, 0, 0)
