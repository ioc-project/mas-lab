#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Lab API + schedule_experiment / await_batch / next_runs."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.lab.benchmark.control import (
    BatchBudget,
    next_runs,
    reset_batches,
    schedule_batch,
    set_batch_runner,
)
from mas.lab.benchmark.execution.roles import PlannedRun
from mas.lab.benchmark.pipeline.assembly import catalog

DATASET = (
    "apiVersion: lab/v1\nkind: Dataset\nmetadata: {name: d}\nspec:\n  items:\n    - {id: q1, inputs: {user: hello}}\n"
)


def _experiment(tmp_path: Path, name: str, scenarios: str = "    - id: s1\n") -> Path:
    path = tmp_path / "experiment.yaml"
    path.write_text(
        f"experiment:\n  name: {name}\n  application:\n    manifest: ./mas.yaml\n"
        f"  scenarios:\n{scenarios}  dataset:\n    path: ./dataset.yaml\n  run:\n    n_runs: 1\n",
        encoding="utf-8",
    )
    (tmp_path / "dataset.yaml").write_text(DATASET, encoding="utf-8")
    (tmp_path / "mas.yaml").write_text("kind: Agent\nmetadata: {name: dummy}\n", encoding="utf-8")
    return path


def _item(n: int) -> dict:
    return {"id": n, "prompt": f"q{n}"}


def test_successive_halving_drops_weakest() -> None:
    runs = [
        PlannedRun("strong-a", _item(1), 0),
        PlannedRun("strong-b", _item(1), 0),
        PlannedRun("weak-c", _item(1), 0),
        PlannedRun("weak-d", _item(1), 0),
    ]
    scores = {"strong-a": 0.91, "strong-b": 0.84, "weak-c": 0.41, "weak-d": 0.12}
    kept = next_runs(runs=runs, scores=scores, strategy="successive_halving", keep_fraction=0.5)
    assert {c.scenario_id for c in kept} == {"strong-a", "strong-b"}


def test_score_driven_keeps_median_and_above() -> None:
    runs = [
        PlannedRun("a", _item(1), 0),
        PlannedRun("b", _item(1), 0),
        PlannedRun("c", _item(1), 0),
    ]
    scores = {"a": 0.9, "b": 0.2, "c": 0.5}
    kept = next_runs(runs=runs, scores=scores, strategy="score_driven")
    assert {c.scenario_id for c in kept} == {"a", "c"}


def test_schedule_experiment_respects_batch_bound(tmp_path: Path) -> None:
    yaml_path = _experiment(tmp_path, "t")
    seen: list[str] = []

    def runner(handle) -> None:  # noqa: ANN001
        seen.append(handle.batch_id)
        handle.status = "complete"

    set_batch_runner(runner)
    budget = BatchBudget(max_scheduled_batches=1)
    try:
        first = schedule_batch(yaml_path, budget=budget)
        assert first.batch_id in seen
        with pytest.raises(ValueError, match="max 1"):
            schedule_batch(yaml_path, budget=budget)
    finally:
        reset_batches()


def test_assembly_catalog_lists_modes_and_control_steps() -> None:
    data = catalog().as_dict()
    assert data["blocks"] == ["generator", "scheduler", "executor", "retrier", "analyzer", "pipeline"]
    assert set(data["stage_modes"]) == {"batch", "live", "mixed"}
    assert "schedule_batch" in data["lab_api"]
    types = {step["type"] for step in data["steps"]}
    assert {"schedule_experiment", "await_batch", "next_runs", "propose_prompts", "inject_experiment"} <= types
    edges = {(e["from"], e["to"]): e["when"] for e in data["allowed_edges"]}
    assert edges[("live", "batch")] == "completeness_gate"
    assert edges[("batch", "batch")] == "complete"
    assert edges[("batch", "live")] == "any"
