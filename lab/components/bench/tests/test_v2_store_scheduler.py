#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from mas.lab.benchmark.pipeline.models import StepOutput
from mas.lab.benchmark.pipeline.adapter import LegacyStepAdapter
from mas.lab.benchmark.pipeline.keys import RunKey
from mas.lab.benchmark.pipeline.scheduler import Node, Scheduler
from mas.lab.benchmark.pipeline.store import ArtifactStore, StoreManifest, compute_node_key
from mas.library.lab.steps.extract.trace_stats import empty_trace_stats, fold_trace_event, public_trace_stats


def test_v2_node_key_merkle() -> None:
    a = compute_node_key("eval_mce", "1", {"m": ["x"]}, "digest-run-a")
    b = compute_node_key("eval_mce", "1", {"m": ["x"]}, "digest-run-b")
    parent_a = compute_node_key("gather", "1", {}, a)
    parent_b = compute_node_key("gather", "1", {}, b)
    other = compute_node_key("gather", "1", {}, compute_node_key("eval_mce", "1", {"m": ["x"]}, "digest-run-c"))
    assert a != b
    assert parent_a != parent_b
    assert other != parent_a


def test_v2_store_atomic(tmp_path) -> None:
    store = ArtifactStore(tmp_path / "artefacts")
    key = compute_node_key("t", "1", {}, "in")
    dest = store.put(
        StoreManifest(key=key, step_type="t", version="1", config={}, input_digests=["in"]),
        files={"out.txt": b"hello"},
    )
    assert (dest / "manifest.json").is_file()
    assert store.exists(key)
    hit = store.get(key)
    assert hit is not None and hit.key == key
    assert not (tmp_path / "artefacts").joinpath("artefact-").exists()


def test_v2_fold_commit_vs_refold() -> None:
    events = [
        {"kind": "llm_call_start"},
        {"kind": "llm_call_end", "tokens_used": {"total_tokens": 3, "prompt_tokens": 2, "completion_tokens": 1}},
    ]
    live = empty_trace_stats()
    for rec in events:
        fold_trace_event(live, rec)
    cold = empty_trace_stats()
    for rec in events:
        fold_trace_event(cold, rec)
    assert public_trace_stats(live)["n_llm_calls"] == public_trace_stats(cold)["n_llm_calls"]


def test_v2_progressive_never_persists() -> None:
    step = SimpleNamespace(type="x", config={}, execute=None)
    adapter = LegacyStepAdapter(step)
    node = Node(
        name="cc3",
        scope="experiment",
        phase="post",
        step=step,
        adapter=adapter,
        refresh="progressive",
    )
    sched = Scheduler([node])
    assert sched.progressive_never_persists(node)


@pytest.mark.asyncio
async def test_v2_scope_gate() -> None:
    class _Step:
        type = "noop"
        config = {}
        name = "n"

        async def execute(self, ctx):
            return StepOutput(data={"ok": True})

    step = _Step()
    adapter = LegacyStepAdapter(step)
    node = Node(name="run-post", scope="run", phase="post", step=step, adapter=adapter, key=RunKey("e", "s", "i", "r1"))
    sched = Scheduler([node])
    await sched.mark_run_terminal(node.key, "ok")
    status = await asyncio.wait_for(sched.drain(), timeout=2)
    assert status["run-post"] == "ok"


@pytest.mark.asyncio
async def test_v2_judge_cap_global() -> None:
    from mas.lab.benchmark.pipeline.scheduler import ResourcePool

    pool = ResourcePool({"judge": 2})
    sem = pool.semaphore("judge")
    assert sem._value == 2


def test_v2_cursor_resume() -> None:
    from mas.lab.benchmark.pipeline.incremental import IncrementalMixin

    class S(IncrementalMixin):
        def execute(self, ctx):
            return None

    step = S()
    key = RunKey("e", "s", "i", "r1")
    step.begin(key, None)
    blob = step.snapshot()
    other = S()
    other.restore(blob)
    assert key.stream_id() in other._folds


@pytest.mark.asyncio
async def test_incremental_mixin_finish_does_not_execute() -> None:
    from mas.lab.benchmark.pipeline.incremental import IncrementalMixin

    class S(IncrementalMixin):
        async def execute(self, ctx):  # noqa: ANN001
            raise AssertionError("finish must not re-run execute")

    step = S()
    key = RunKey("e", "s", "i", "r1")
    step.begin(key, None)
    out = await step.finish(None)
    assert out.metadata.get("from_fold") is True
