#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Plan -> run one shard -> read its results tree: the CI round trip on the loopback fixture."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from mas.lab.benchmark.execution.planner import plan_experiment
from mas.lab.benchmark.execution.wire import make_shards, plan_document, read_results, write_plan
from scaling_support import (
    isolate_lab_env,
    lab_ports,
    prepare_lab,
    results_rows,
    stop_spawned_listeners,
    write_schedule_overlay,
)


@pytest.fixture
def lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    isolate_lab_env(monkeypatch, tmp_path)
    path = prepare_lab(tmp_path)
    yield path
    stop_spawned_listeners(*lab_ports(path))


@pytest.mark.asyncio
async def test_a_shard_runs_only_its_runs_and_leaves_a_self_contained_tree(lab: Path, tmp_path: Path) -> None:
    from mas.lab.benchmark.schedule.run_batch import run_mas_benchmark

    overlay = write_schedule_overlay(tmp_path, parallel=1, limit=3)
    _, runs = plan_experiment(lab / "experiment.yaml", experiment_overlays=[str(overlay)])
    assert len(runs) >= 3
    shards = make_shards(runs, by="run")
    mine = shards[1]  # the second shard of the matrix, run on "its own runner"
    plan = plan_document(
        mine,
        experiment="lab",
        experiment_yaml=str(lab / "experiment.yaml"),
        generator="cartesian",
        scheduler="coverage",
        attempts_before={mine[0].id: 1},
    )
    shard_file = write_plan(tmp_path / "shard-1.json", plan)

    out = tmp_path / f"out-{time.time_ns()}"
    cache = tmp_path / "trace-cache"
    cache.mkdir()
    try:
        ok = await run_mas_benchmark(
            lab / "experiment.yaml",
            progress=False,
            force=True,
            output_dir=out,
            trace_cache_dir=cache,
            experiment_overlays=[str(overlay)],
            plan_file=shard_file,
        )
    finally:
        stop_spawned_listeners(*lab_ports(lab))
    assert ok
    rows = results_rows(out)
    assert {row["run_id"] for row in rows} == {c.id for c in mine}

    results = read_results(out, mine)
    assert all(res.complete for res in results.values())
    # attempts carry over from the earlier round: one spent before, one used now
    assert results[mine[0].id].attempts == 2
    # the folder carries its own result, no trace-cache lookup needed
    run_dir = out / mine[0].scenario_id / f"item{mine[0].item['id']}" / "r1"
    assert json.loads((run_dir / "result.json").read_text(encoding="utf-8"))["status"] == "ok"
    # runs of other shards were never touched
    others = [c for c in runs if c.id not in {m.id for m in mine}]
    assert all(res.status == "missing" for res in read_results(out, others).values())
