#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A scenario's ``infra_refs`` replace the workspace infra files for its runs only."""

from __future__ import annotations

from pathlib import Path

from mas.lab.benchmark.schedule.run_batch.execute import _infra_refs_for_scenario
from mas.lab.benchmark.schedule.run_batch.prepare import _ref_path
from mas.lab.lab.config.scenario import MASScenarioSpec


def test_infra_refs_are_read_from_the_scenario(tmp_path: Path) -> None:
    spec = MASScenarioSpec.from_dict({"id": "a", "infra_refs": ["arms/a/tools.infra.yaml"]}, tmp_path)
    assert spec.infra_refs == ["arms/a/tools.infra.yaml"]
    assert MASScenarioSpec.from_dict({"id": "b"}, tmp_path).infra_refs == []


def test_relative_refs_resolve_against_the_experiment_file(tmp_path: Path) -> None:
    assert _ref_path("arms/a.yaml", tmp_path) == str((tmp_path / "arms" / "a.yaml").resolve())
    assert _ref_path("/abs/a.yaml", tmp_path) == "/abs/a.yaml"


def test_a_scenario_with_refs_replaces_the_workspace_refs_and_others_keep_them() -> None:
    class Prepared:
        infra_refs = ["ws.yaml"]
        scenario_infra_refs = {"a": ["a.yaml"]}

    assert _infra_refs_for_scenario("a", Prepared()) == ["a.yaml"]  # type: ignore[arg-type]
    assert _infra_refs_for_scenario("b", Prepared()) == ["ws.yaml"]  # type: ignore[arg-type]
