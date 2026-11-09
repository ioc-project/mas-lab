#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""End-to-end parse + observable effect tests for the design/schedule split."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.lab.benchmark.execution.plan import build_execution_plan
from mas.lab.deprecations import clear_deprecation_warnings
from mas.lab.lab.config import MASExperimentConfig


@pytest.fixture(autouse=True)
def _clear_deprecations() -> None:
    clear_deprecation_warnings()


def _load(tmp_path: Path, body: str) -> MASExperimentConfig:
    path = tmp_path / "experiment.yaml"
    path.write_text(
        "experiment:\n"
        "  name: split-test\n"
        "  application:\n"
        "    manifest: ./mas.yaml\n"
        "  scenarios:\n"
        "    - id: s1\n"
        "    - id: s2\n"
        f"{body}",
        encoding="utf-8",
    )
    return MASExperimentConfig.from_yaml(path)


def test_new_shape_parses_without_scheduler_fields_in_design(tmp_path: Path) -> None:
    cfg = _load(
        tmp_path,
        "  run:\n    n_runs: 4\n  design:\n    mode: cartesian\n    max_executions: 50\n",
    )
    assert cfg.n_runs == 4
    assert cfg.design.mode == "cartesian"
    assert cfg.design.max_executions == 50
    assert cfg.schedule.ordering == "coverage"
    assert "execution:" not in cfg._path.read_text()


def test_schedule_ordering_changes_plan_not_n_runs(tmp_path: Path) -> None:
    items = [{"id": "a"}, {"id": "b"}]
    coverage = build_execution_plan(["s1", "s2"], items, 2, strategy="coverage")
    depth = build_execution_plan(["s1", "s2"], items, 2, strategy="depth")
    assert len(coverage) == len(depth) == 8
    assert coverage[0][2] == 0 and coverage[1][2] == 0
    assert depth[0] == ("s1", {"id": "a"}, 0)
    assert depth[1] == ("s1", {"id": "a"}, 1)
    assert depth[2] == ("s1", {"id": "b"}, 0)

    cfg = _load(
        tmp_path,
        "  run:\n    n_runs: 2\n  schedule:\n    ordering: depth\n    parallel_scenarios: 9\n",
    )
    assert cfg.n_runs == 2
    assert cfg.schedule.parallel_scenarios == 9
    plan = build_execution_plan(cfg.scenario_ids(), items, cfg.n_runs, strategy=cfg.schedule.ordering)
    assert plan[1] == ("s1", {"id": "a"}, 1)


def test_design_max_executions_changes_which_cells_are_legal(tmp_path: Path) -> None:
    cfg = _load(
        tmp_path,
        "  run:\n    n_runs: 3\n  design:\n    mode: cartesian\n    max_executions: 2\n",
    )
    with pytest.raises(RuntimeError, match="design.max_executions=2"):
        build_execution_plan(
            cfg.scenario_ids(),
            [{"id": "a"}, {"id": "b"}],
            cfg.n_runs,
            design=cfg.design.as_plan_dict(),
        )


def test_new_keys_win_over_legacy_execution(tmp_path: Path) -> None:
    cfg = _load(
        tmp_path,
        "  run:\n"
        "    n_runs: 2\n"
        "  execution:\n"
        "    parallel_scenarios: 3\n"
        "    strategy: depth\n"
        "  schedule:\n"
        "    parallel_scenarios: 11\n"
        "    ordering: coverage\n",
    )
    assert cfg.schedule.parallel_scenarios == 11
    assert cfg.schedule.ordering == "coverage"
    assert cfg.execution.parallel_scenarios == 11
    assert cfg.n_runs == 2


def test_legacy_max_attempts_lands_on_schedule(tmp_path: Path) -> None:
    cfg = _load(
        tmp_path,
        "  run:\n    n_runs: 1\n  execution:\n    max_attempts: 1\n    retry_backoff_s: 0\n",
    )
    assert cfg.schedule.max_attempts == 1
    assert cfg.schedule.retry_backoff_s == 0.0
    assert cfg.execution.max_attempts == 1
