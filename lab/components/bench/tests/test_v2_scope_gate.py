#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import asyncio
import pytest

from mas.lab.benchmark.pipeline.models import StepOutput
from mas.lab.benchmark.pipeline.adapter import LegacyStepAdapter
from mas.lab.benchmark.pipeline.keys import RunKey
from mas.lab.benchmark.pipeline.scheduler import Node, ResourcePool, Scheduler


class _Step:
    def __init__(self, name: str) -> None:
        self.name = name
        self.type = "noop"
        self.config = {}
        self.ran = 0

    async def execute(self, ctx):
        self.ran += 1
        return StepOutput(data={"ok": True})


@pytest.mark.asyncio
async def test_v2_scope_gate() -> None:
    item_step = _Step("item-post")
    run_a = _Step("run-a")
    run_b = _Step("run-b")
    nodes = [
        Node(name="run-a", scope="run", phase="post", step=run_a, adapter=LegacyStepAdapter(run_a), key=RunKey("e", "s", "item1", "r1")),
        Node(name="run-b", scope="run", phase="post", step=run_b, adapter=LegacyStepAdapter(run_b), key=RunKey("e", "s", "item1", "r2")),
        Node(name="item-post", scope="item", phase="post", step=item_step, adapter=LegacyStepAdapter(item_step), parent="item:s/item1"),
    ]
    sched = Scheduler(nodes, resources=ResourcePool())
    sched.set_gate("item:s/item1", 2)
    await sched.mark_run_terminal(RunKey("e", "s", "item1", "r1"), "ok")
    assert item_step.ran == 0
    await sched.mark_run_terminal(RunKey("e", "s", "item1", "r2"), "failed")
    await asyncio.wait_for(sched.drain(), timeout=2)
    assert run_a.ran == 1 and run_b.ran == 1
    assert item_step.ran == 1


@pytest.mark.asyncio
async def test_v2_scope_gates_chain_item_scenario_experiment() -> None:
    from mas.lab.benchmark.pipeline.scheduler import install_scope_gates

    keys = [
        RunKey("e", "s1", "i1", "r1"),
        RunKey("e", "s1", "i2", "r1"),
    ]
    item_a = _Step("item-a")
    item_b = _Step("item-b")
    sc_step = _Step("sc-post")
    exp_step = _Step("exp-post")
    run_a = _Step("run-a")
    run_b = _Step("run-b")
    nodes = [
        Node(
            name="run-a",
            scope="run",
            phase="post",
            step=run_a,
            adapter=LegacyStepAdapter(run_a),
            key=keys[0],
        ),
        Node(
            name="run-b",
            scope="run",
            phase="post",
            step=run_b,
            adapter=LegacyStepAdapter(run_b),
            key=keys[1],
        ),
        Node(
            name="item-a",
            scope="item",
            phase="post",
            step=item_a,
            adapter=LegacyStepAdapter(item_a),
            parent="item:s1/i1",
        ),
        Node(
            name="item-b",
            scope="item",
            phase="post",
            step=item_b,
            adapter=LegacyStepAdapter(item_b),
            parent="item:s1/i2",
        ),
        Node(
            name="sc-post",
            scope="scenario",
            phase="post",
            step=sc_step,
            adapter=LegacyStepAdapter(sc_step),
            parent="scenario:s1",
        ),
        Node(
            name="exp-post",
            scope="experiment",
            phase="post",
            step=exp_step,
            adapter=LegacyStepAdapter(exp_step),
            parent="experiment",
        ),
    ]
    sched = Scheduler(nodes, resources=ResourcePool())
    install_scope_gates(sched, keys)
    drain = asyncio.create_task(sched.drain())
    await asyncio.sleep(0.02)
    assert exp_step.ran == 0 and sc_step.ran == 0
    await sched.mark_run_terminal(keys[0], "ok")
    await asyncio.sleep(0.02)
    assert exp_step.ran == 0
    await sched.mark_run_terminal(keys[1], "ok")
    await asyncio.wait_for(drain, timeout=2)
    assert run_a.ran == 1 and run_b.ran == 1
    assert item_a.ran == 1 and item_b.ran == 1
    assert sc_step.ran == 1
    assert exp_step.ran == 1


def test_v2_pre_per_run() -> None:
    from mas.lab.benchmark.pipeline.expand import planned_nodes
    from pathlib import Path

    keys = [
        RunKey("e", "baseline", "item1", "r1"),
        RunKey("e", "baseline", "item1", "r2"),
    ]
    nodes = planned_nodes(keys, Path("/tmp/out"))
    assert len(nodes["run"]) == 2
    assert len(nodes["item"]) == 1
