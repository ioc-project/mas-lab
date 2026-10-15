#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from pathlib import Path

from mas.lab.benchmark.pipeline.expand import expand_step_dicts, planned_nodes
from mas.lab.benchmark.pipeline.keys import RunKey
from mas.lab.benchmark.pipeline.scheduler import ResourcePool
from mas.lab.lab.config.pipeline import PipelineStepSpec


def test_v2_expand_parity(tmp_path: Path) -> None:
    keys = [
        RunKey("demo", "alpha", "item1", "r1"),
        RunKey("demo", "bravo", "item1", "r1"),
    ]
    planned = planned_nodes(keys, tmp_path)
    assert {n.scenario for n in planned["run"]} == {"alpha", "bravo"}
    spec = PipelineStepSpec(
        name="stats",
        type="extract_trace_stats",
        scope="run",
        phase="post",
    )
    dicts = expand_step_dicts(
        [spec], keys=keys, output_dir=tmp_path, scenario_ids=["alpha", "bravo"]
    )
    assert dicts, "plan-based expansion must emit at least one step dict"
    blob = json.dumps(dicts, default=str)
    assert "alpha" in blob and "bravo" in blob
    names = [str(d.get("name") or "") for d in dicts]
    assert any("stats" in name for name in names)
    assert len(dicts) == 2
    assert all(d.get("scope") == "run" for d in dicts)


def test_v2_ordering_property() -> None:
    """v2 order is a valid linearization: depends_on edges are respected."""
    from mas.lab.benchmark.pipeline.scheduler import Node, Scheduler
    from mas.lab.benchmark.pipeline.models import StepOutput
    from mas.lab.benchmark.pipeline.adapter import LegacyStepAdapter
    import asyncio

    order: list[str] = []

    class _Step:
        def __init__(self, name: str) -> None:
            self.name = name
            self.type = "noop"
            self.config = {}

        async def execute(self, ctx):
            order.append(self.name)
            return StepOutput(data={})

    a, b, c = _Step("a"), _Step("b"), _Step("c")
    nodes = [
        Node(name="a", scope="experiment", phase="post", step=a, adapter=LegacyStepAdapter(a)),
        Node(name="b", scope="experiment", phase="post", step=b, adapter=LegacyStepAdapter(b), depends_on=["a"]),
        Node(name="c", scope="experiment", phase="post", step=c, adapter=LegacyStepAdapter(c), depends_on=["b"]),
    ]
    sched = Scheduler(nodes)
    asyncio.run(sched.drain())
    assert order == ["a", "b", "c"]


def test_v2_judge_cap_global() -> None:
    import asyncio

    pool = ResourcePool()
    sem = pool.semaphore("judge")
    running = 0
    peak = 0

    async def worker() -> None:
        nonlocal running, peak
        async with sem:
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.02)
            running -= 1

    async def _go() -> None:
        await asyncio.gather(*[worker() for _ in range(5)])

    asyncio.run(_go())
    assert peak == 2


def test_v2_cursor_resume() -> None:
    import asyncio
    from mas.lab.benchmark.pipeline.channel import InprocChannel

    async def _go():
        ch = InprocChannel()
        ch.bind_loop(asyncio.get_running_loop())
        sid = "s"
        for i in range(3):
            ch.publish_sync(sid, {"kind": "x", "i": i})
        seen = []
        async for frame in ch.subscribe(sid, from_offset=1):
            seen.append(frame.offset)
            if frame.offset >= 2:
                break
        assert seen == [1, 2]

    asyncio.run(_go())
