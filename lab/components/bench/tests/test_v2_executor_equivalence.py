#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mas.lab.benchmark.pipeline import Pipeline, PipelineConfig, PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.executor import PipelineExecutor
from mas.lab.benchmark.pipeline.adapter import LegacyStepAdapter
from mas.lab.benchmark.pipeline.scheduler import Node, Scheduler


class _Record(PipelineStep):
    type = "record"

    async def execute(self, ctx) -> StepOutput:  # noqa: ANN001
        order = ctx.step_outputs.setdefault("_order", StepOutput(data={"names": []}))
        names = list(order.data.get("names") or [])
        names.append(self.name)
        ctx.step_outputs["_order"] = StepOutput(data={"names": names})
        return StepOutput(data={"name": self.name, "n": len(names)})


class _Boom(PipelineStep):
    type = "boom"

    async def execute(self, ctx) -> StepOutput:  # noqa: ANN001
        raise RuntimeError("boom")


def _pipeline(*steps) -> Pipeline:
    return Pipeline(config=PipelineConfig(name="eq"), steps=list(steps))


def test_scheduler_linearization_respects_depends_on() -> None:
    order: list[str] = []

    class _Step:
        def __init__(self, name: str) -> None:
            self.name = name
            self.type = "noop"
            self.config = {}

        async def execute(self, ctx):  # noqa: ANN001
            order.append(self.name)
            return StepOutput(data={})

    a, b, c = _Step("a"), _Step("b"), _Step("c")
    nodes = [
        Node(name="a", scope="experiment", phase="post", step=a, adapter=LegacyStepAdapter(a)),
        Node(
            name="b",
            scope="experiment",
            phase="post",
            step=b,
            adapter=LegacyStepAdapter(b),
            depends_on=["a"],
        ),
        Node(
            name="c",
            scope="experiment",
            phase="post",
            step=c,
            adapter=LegacyStepAdapter(c),
            depends_on=["b"],
        ),
    ]
    asyncio.run(Scheduler(nodes).drain())
    assert order == ["a", "b", "c"]


def test_scheduler_skips_dependents_runs_siblings() -> None:
    seen: list[str] = []

    class _Ok:
        def __init__(self, name: str) -> None:
            self.name = name
            self.type = "noop"
            self.config = {}

        async def execute(self, ctx):  # noqa: ANN001
            seen.append(self.name)
            return StepOutput(data={})

    class _Fail(_Ok):
        async def execute(self, ctx):  # noqa: ANN001
            seen.append(self.name)
            raise RuntimeError("nope")

    a, b, c, d = _Fail("a"), _Ok("b"), _Ok("c"), _Ok("d")
    nodes = [
        Node(name="a", scope="experiment", phase="post", step=a, adapter=LegacyStepAdapter(a)),
        Node(
            name="b",
            scope="experiment",
            phase="post",
            step=b,
            adapter=LegacyStepAdapter(b),
            depends_on=["a"],
        ),
        Node(name="c", scope="experiment", phase="post", step=c, adapter=LegacyStepAdapter(c)),
        Node(
            name="d",
            scope="experiment",
            phase="post",
            step=d,
            adapter=LegacyStepAdapter(d),
            depends_on=["c"],
        ),
    ]
    statuses = asyncio.run(Scheduler(nodes).drain())
    assert statuses["a"] == "failed"
    assert statuses["b"] == "skipped"
    assert statuses["c"] == "ok"
    assert statuses["d"] == "ok"
    assert "b" not in seen or seen.count("b") == 0


@pytest.mark.asyncio
async def test_executor_uses_scheduler_same_outputs(tmp_path: Path) -> None:
    pipeline = _pipeline(
        _Record(name="a", config={}),
        _Record(name="b", config={}, depends_on=["a"]),
        _Record(name="c", config={}, depends_on=["a"]),
    )
    result = await PipelineExecutor(pipeline, output_dir=tmp_path).run()
    assert result.success
    assert set(result.executed_steps) >= {"a", "b", "c"}
    assert result.step_outputs["b"].data["name"] == "b"
    assert result.step_outputs["c"].data["name"] == "c"
    assert result.step_outputs["a"].data["n"] == 1


@pytest.mark.asyncio
async def test_executor_diamond_same_outputs(tmp_path: Path) -> None:
    pipeline = _pipeline(
        _Record(name="a", config={}),
        _Record(name="b", config={}, depends_on=["a"]),
        _Record(name="c", config={}, depends_on=["a"]),
        _Record(name="d", config={}, depends_on=["b", "c"]),
    )
    result = await PipelineExecutor(pipeline, output_dir=tmp_path).run()
    assert result.success
    assert set(result.executed_steps) >= {"a", "b", "c", "d"}
    assert result.step_outputs["d"].data["name"] == "d"
    # a before b and c; d after both
    names = result.step_outputs["_order"].data["names"]
    assert names[0] == "a"
    assert names[-1] == "d"
    assert names.index("b") < names.index("d")
    assert names.index("c") < names.index("d")


@pytest.mark.asyncio
async def test_executor_failed_dep_skips_child(tmp_path: Path) -> None:
    pipeline = _pipeline(
        _Boom(name="a", config={}),
        _Record(name="b", config={}, depends_on=["a"]),
        _Record(name="c", config={}),
    )
    result = await PipelineExecutor(pipeline, output_dir=tmp_path).run()
    assert not result.success
    assert "a" in result.failed_steps
    assert "b" in result.failed_steps
    assert "c" in result.executed_steps
    assert "c" in result.step_outputs
