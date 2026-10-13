#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from mas.lab.benchmark.pipeline.models import StepOutput
from mas.lab.benchmark.pipeline.adapter import LegacyStepAdapter
from mas.lab.benchmark.pipeline.channel import InprocChannel, set_channel
from mas.lab.benchmark.pipeline.fanout import classify_edges, edge_kind
from mas.lab.benchmark.pipeline.scheduler import Node, ResourcePool, Scheduler


class _Producer:
    def __init__(self, name: str, n: int = 8, channel: InprocChannel | None = None) -> None:
        self.name = name
        self.type = "producer"
        self.config = {}
        self.n = n
        self.channel = channel
        self.started_at = 0.0

    async def execute(self, ctx):  # noqa: ANN001
        del ctx
        self.started_at = time.perf_counter()
        assert self.channel is not None
        for i in range(self.n):
            self.channel.publish_sync(self.name, {"kind": "rec", "i": i})
            await asyncio.sleep(0.005)
        self.channel.seal_sync(self.name)
        return StepOutput(data={"n": self.n})


class _Fold:
    def __init__(self, name: str) -> None:
        self.name = name
        self.type = "fold"
        self.config = {}
        self.seen: list[int] = []
        self.first_at = 0.0
        self.last_at = 0.0

    def on_batch(self, batch, ctx):  # noqa: ANN001
        del ctx
        for rec in batch:
            if not self.seen:
                self.first_at = time.perf_counter()
            self.seen.append(int(rec.get("i", -1)))
            self.last_at = time.perf_counter()

    async def finish(self, ctx):  # noqa: ANN001
        del ctx
        return StepOutput(data={"n": len(self.seen)})


class _Join:
    def __init__(self, name: str) -> None:
        self.name = name
        self.type = "join"
        self.config = {}
        self.started_at = 0.0

    async def execute(self, ctx):  # noqa: ANN001
        del ctx
        self.started_at = time.perf_counter()
        return StepOutput(data={"joined": True})


def _node(step, *, deps: list[str] | None = None) -> Node:
    return Node(
        name=step.name,
        scope="experiment",
        phase="post",
        step=step,
        adapter=LegacyStepAdapter(step),
        depends_on=list(deps or []),
    )


def test_classify_fork_vs_join() -> None:
    a, b, c, d = _Producer("a"), _Fold("b"), _Fold("c"), _Join("d")
    nodes = [_node(a), _node(b, deps=["a"]), _node(c, deps=["a"]), _node(d, deps=["b", "c"])]
    edges = classify_edges(nodes)
    assert edge_kind(edges, "a", "b") == "fork"
    assert edge_kind(edges, "a", "c") == "fork"
    assert edge_kind(edges, "b", "d") == "join"
    assert edge_kind(edges, "c", "d") == "join"


def test_record_level_fork_overlaps() -> None:
    channel = InprocChannel()
    set_channel(channel)
    prod = _Producer("a", n=10, channel=channel)
    left, right = _Fold("b"), _Fold("c")
    nodes = [_node(prod), _node(left, deps=["a"]), _node(right, deps=["a"])]

    async def _main() -> dict[str, str]:
        channel.bind_loop(asyncio.get_running_loop())
        return await Scheduler(
            nodes,
            resources=ResourcePool({"cpu": 8}),
            max_active=8,
            channel=channel,
        ).drain()

    statuses = asyncio.run(_main())
    assert statuses == {"a": "ok", "b": "ok", "c": "ok"}
    assert left.seen == list(range(10))
    assert right.seen == list(range(10))
    # Both consumers started before the producer finished publishing.
    assert left.first_at < prod.started_at + 0.2
    assert right.first_at < prod.started_at + 0.2
    overlap = min(left.last_at, right.last_at) > max(left.first_at, right.first_at)
    assert overlap


def test_join_waits_for_both_artifacts() -> None:
    channel = InprocChannel()
    set_channel(channel)
    left_p, right_p = _Producer("a", n=3, channel=channel), _Producer("b", n=3, channel=channel)
    join = _Join("c")
    done_a = 0.0
    done_b = 0.0

    async def _run(node) -> StepOutput:
        nonlocal done_a, done_b
        if node.name == "a":
            out = await left_p.execute(None)
            done_a = time.perf_counter()
            return out
        if node.name == "b":
            out = await right_p.execute(None)
            done_b = time.perf_counter()
            return out
        if node.name == "c":
            return await join.execute(None)
        raise AssertionError(node.name)

    nodes = [_node(left_p), _node(right_p), _node(join, deps=["a", "b"])]

    async def _main() -> dict[str, str]:
        channel.bind_loop(asyncio.get_running_loop())
        return await Scheduler(nodes, run=_run, max_active=8, channel=channel).drain()

    statuses = asyncio.run(_main())
    assert statuses["c"] == "ok"
    assert join.started_at >= done_a
    assert join.started_at >= done_b


def test_max_active_caps_concurrent_steps() -> None:
    current = 0
    peak = 0

    class _Hold:
        def __init__(self, name: str) -> None:
            self.name = name
            self.type = "hold"
            self.config = {}

        async def execute(self, ctx):  # noqa: ANN001
            nonlocal current, peak
            del ctx
            current += 1
            peak = max(peak, current)
            await asyncio.sleep(0.02)
            current -= 1
            return StepOutput(data={})

    steps = [_Hold(n) for n in ("a", "b", "c", "d")]
    nodes = [_node(s) for s in steps]

    async def _run(node) -> StepOutput:
        return await node.step.execute(None)

    asyncio.run(Scheduler(nodes, run=_run, max_active=2).drain())
    assert peak <= 2
    assert peak >= 1


@pytest.mark.asyncio
async def test_executor_pumps_record_fold_when_run_is_set(tmp_path: Path) -> None:
    """Production PipelineExecutor.bind injects ``run=``; fold consumers must still pump."""
    from mas.lab.benchmark.pipeline import Pipeline, PipelineConfig, PipelineStep
    from mas.lab.benchmark.pipeline.executor import PipelineExecutor
    from mas.lab.benchmark.pipeline.channel import InprocChannel, set_channel
    from mas.lab.benchmark.pipeline.fanout import uses_record_fold

    channel = InprocChannel()
    set_channel(channel)
    channel.bind_loop(asyncio.get_running_loop())

    class _Prod(PipelineStep):
        type = "prod"

        async def execute(self, ctx) -> StepOutput:  # noqa: ANN001
            del ctx
            for i in range(8):
                channel.publish_sync(self.name, {"kind": "rec", "i": i})
                await asyncio.sleep(0.003)
            channel.seal_sync(self.name)
            return StepOutput(data={"n": 8})

    class _FoldStep(PipelineStep):
        type = "fold"

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.seen: list[int] = []
            self.first_at = 0.0

        def on_batch(self, batch, ctx):  # noqa: ANN001
            del ctx
            for rec in batch:
                if not self.seen:
                    self.first_at = time.perf_counter()
                self.seen.append(int(rec.get("i", -1)))

        async def finish(self, ctx) -> StepOutput:  # noqa: ANN001
            del ctx
            return StepOutput(data={"n": len(self.seen)})

        async def execute(self, ctx) -> StepOutput:  # noqa: ANN001
            raise AssertionError("record-fold consumers must not fall back to execute")

    prod = _Prod(name="a", config={})
    left, right = _FoldStep(name="b", config={}, depends_on=["a"]), _FoldStep(
        name="c", config={}, depends_on=["a"]
    )
    assert uses_record_fold(left)
    pipeline = Pipeline(
        config=PipelineConfig(name="fold"),
        steps=[prod, left, right],
    )
    t0 = time.perf_counter()
    result = await PipelineExecutor(pipeline, output_dir=tmp_path).run(parallel=True)
    assert result.success
    assert left.seen == list(range(8))
    assert right.seen == list(range(8))
    assert left.first_at < t0 + 0.5
    assert result.step_outputs["b"].data["n"] == 8
    assert result.step_outputs["c"].data["n"] == 8


@pytest.mark.asyncio
async def test_ctx_publish_during_execute_seals_without_redump(tmp_path: Path) -> None:
    from mas.lab.benchmark.pipeline import Pipeline, PipelineConfig, PipelineStep
    from mas.lab.benchmark.pipeline.executor import PipelineExecutor
    from mas.lab.benchmark.pipeline.channel import InprocChannel, get_channel, set_channel

    channel = InprocChannel()
    set_channel(channel)
    channel.bind_loop(asyncio.get_running_loop())

    class _Stream(PipelineStep):
        type = "stream"

        async def execute(self, ctx) -> StepOutput:  # noqa: ANN001
            for i in range(3):
                ctx.publish({"kind": "rec", "i": i})
            return StepOutput(data={"records": [{"kind": "rec", "i": 99}]})

    class _FoldStep(PipelineStep):
        type = "fold"

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.seen: list[int] = []

        def on_batch(self, batch, ctx):  # noqa: ANN001
            del ctx
            for rec in batch:
                self.seen.append(int(rec.get("i", -1)))

        async def finish(self, ctx) -> StepOutput:  # noqa: ANN001
            del ctx
            return StepOutput(data={"n": len(self.seen)})

        async def execute(self, ctx) -> StepOutput:  # noqa: ANN001
            raise AssertionError("fold must pump")

    prod = _Stream(name="a", config={})
    fold = _FoldStep(name="b", config={}, depends_on=["a"])
    pipeline = Pipeline(config=PipelineConfig(name="pub"), steps=[prod, fold])
    result = await PipelineExecutor(pipeline, output_dir=tmp_path).run()
    assert result.success
    assert fold.seen == [0, 1, 2]
    assert get_channel().is_sealed("a")
