#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Scheduler plugins: new spelling, old spelling, third-party libraries."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from mas.lab.benchmark.execution.plan import build_execution_plan, plan_kwargs
from mas.lab.deprecations import clear_deprecation_warnings
from mas.lab.lab.config import ExperimentScheduleSpec, MASExperimentConfig

SCENARIOS = ["s1", "s2"]
ITEMS = [{"id": "a"}, {"id": "b"}]


def _ids(plan) -> list[tuple[str, str, int]]:
    return [(sid, item["id"], run_idx) for sid, item, run_idx in plan]


def _load(tmp_path: Path, body: str) -> MASExperimentConfig:
    path = tmp_path / "experiment.yaml"
    path.write_text(
        "experiment:\n"
        "  name: scheduler-test\n"
        "  application:\n"
        "    manifest: ./mas.yaml\n"
        "  scenarios:\n"
        "    - id: s1\n"
        "    - id: s2\n"
        f"{body}",
        encoding="utf-8",
    )
    return MASExperimentConfig.from_yaml(path)


@pytest.fixture(autouse=True)
def _clear_deprecations() -> None:
    clear_deprecation_warnings()


def test_old_ordering_and_new_plugin_produce_the_same_plan(tmp_path: Path) -> None:
    old = _load(tmp_path, "  run:\n    n_runs: 2\n  schedule:\n    ordering: depth\n")
    new = _load(tmp_path, "  run:\n    n_runs: 2\n  schedule:\n    plugin: depth\n")
    old_plan = build_execution_plan(SCENARIOS, ITEMS, old.n_runs, schedule={"plugin": old.schedule.plugin})
    new_plan = build_execution_plan(SCENARIOS, ITEMS, new.n_runs, schedule={"plugin": new.schedule.plugin})
    assert _ids(old_plan) == _ids(new_plan)
    assert old.schedule.plugin == new.schedule.plugin == "depth"


def test_old_schedule_ordering_warns(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    with caplog.at_level(logging.WARNING, logger="mas.lab.deprecations"):
        cfg = _load(tmp_path, "  schedule:\n    ordering: depth\n")
    assert "experiment.schedule.ordering" in caplog.text
    assert cfg.schedule.plugin == "depth"


def test_new_schedule_plugin_does_not_warn(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    with caplog.at_level(logging.WARNING, logger="mas.lab.deprecations"):
        _load(tmp_path, "  schedule:\n    plugin: coverage\n")
    assert "experiment.schedule.ordering" not in caplog.text


def test_mixing_plugin_and_ordering_is_an_error() -> None:
    with pytest.raises(ValueError, match="not both"):
        ExperimentScheduleSpec.from_dict({"plugin": "coverage", "ordering": "depth"})


def test_unknown_scheduler_names_the_registered_list() -> None:
    with pytest.raises(ValueError, match=r"unknown scheduler 'shuffle'; registered:"):
        ExperimentScheduleSpec.from_dict({"plugin": "shuffle"})


def test_omitted_plugin_uses_generator_default_for_cartesian() -> None:
    plan = build_execution_plan(SCENARIOS, ITEMS, 2)
    assert _ids(plan)[4] == ("s1", "a", 1)


def test_schedule_as_plan_dict_keeps_plugin_and_params() -> None:
    spec = ExperimentScheduleSpec.from_dict({"plugin": "coverage", "params": {}})
    assert spec.as_plan_dict() == {"plugin": "coverage", "params": {}, "parallel_scenarios": 4}
    omitted = ExperimentScheduleSpec.from_dict({})
    assert "plugin" not in omitted.as_plan_dict()
    assert omitted.as_plan_dict()["params"] == {}
    assert omitted.as_plan_dict()["parallel_scenarios"] == 4


def test_schedule_params_reach_the_plan_builder() -> None:
    spec = ExperimentScheduleSpec(plugin="coverage", params={"seed": 1})
    with pytest.raises(ValueError, match="unknown key"):
        build_execution_plan(SCENARIOS, ITEMS, 1, schedule=spec.as_plan_dict())


def test_cli_plugin_override_reorders_coupled() -> None:
    exp = SimpleNamespace(
        design={
            "plugin": "coupled",
            "params": {"couplings": [{"scenario": "s1", "items": ["a"]}, {"scenario": "s2", "items": ["b"]}]},
        },
        schedule=None,
    )
    generated = build_execution_plan(SCENARIOS, ITEMS, 2, **plan_kwargs(exp))
    covered = build_execution_plan(SCENARIOS, ITEMS, 2, **plan_kwargs(exp, plugin_override="coverage"))
    assert _ids(generated) == [("s1", "a", 0), ("s1", "a", 1), ("s2", "b", 0), ("s2", "b", 1)]
    assert _ids(covered) == [("s1", "a", 0), ("s2", "b", 0), ("s1", "a", 1), ("s2", "b", 1)]


def test_explicit_coverage_reorders_coupled() -> None:
    design = {
        "plugin": "coupled",
        "params": {"couplings": [{"scenario": "s1", "items": ["a"]}, {"scenario": "s2", "items": ["b"]}]},
    }
    generated = build_execution_plan(SCENARIOS, ITEMS, 2, design=design)
    covered = build_execution_plan(SCENARIOS, ITEMS, 2, design=design, schedule={"plugin": "coverage"})
    assert _ids(generated) == [("s1", "a", 0), ("s1", "a", 1), ("s2", "b", 0), ("s2", "b", 1)]
    assert _ids(covered) == [("s1", "a", 0), ("s2", "b", 0), ("s1", "a", 1), ("s2", "b", 1)]


def test_third_party_scheduler_from_test_library(role_plugins_lib) -> None:
    ExperimentScheduleSpec.from_dict({"plugin": "longest_first"})
    plan = build_execution_plan(["ab", "z"], ITEMS, 1, schedule={"plugin": "longest_first"})
    assert _ids(plan)[0][0] == "ab"
    preferred = ExperimentScheduleSpec.from_dict({"plugin": "longest_first", "params": {"prefer": "z"}})
    preferred_plan = build_execution_plan(["ab", "z"], ITEMS, 1, schedule=preferred.as_plan_dict())
    assert _ids(preferred_plan)[0][0] == "z"
    with pytest.raises(ValueError, match="params."):
        ExperimentScheduleSpec.from_dict({"plugin": "longest_first", "params": {"seed": 1}})
    with pytest.raises(ValueError, match=r"unknown scheduler 'nope'; registered:"):
        ExperimentScheduleSpec.from_dict({"plugin": "nope"})


def test_strategy_flag_is_gone() -> None:
    from click.testing import CliRunner
    from mas.lab.cli.commands.benchmark.run import run_cmd

    result = CliRunner().invoke(run_cmd, ["x.yaml", "--strategy", "coverage"])
    assert result.exit_code != 0
    assert "No such option: --strategy" in result.output
