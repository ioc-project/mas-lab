#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Scenario generator plugins: new spelling, old spelling, third-party libraries."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.lab.benchmark.execution.plan import build_execution_plan
from mas.lab.deprecations import clear_deprecation_warnings
from mas.lab.lab.config import ExperimentDesignSpec, MASExperimentConfig

SCENARIOS = ["s1", "s2"]
ITEMS = [{"id": "a"}, {"id": "b"}]


def _ids(plan) -> list[tuple[str, str, int]]:
    return [(sid, item["id"], run_idx) for sid, item, run_idx in plan]


def _load(tmp_path: Path, body: str) -> MASExperimentConfig:
    path = tmp_path / "experiment.yaml"
    path.write_text(
        "experiment:\n"
        "  name: generator-test\n"
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


def test_old_and_new_spelling_produce_the_same_plan(tmp_path: Path) -> None:
    old = _load(
        tmp_path,
        "  run:\n    n_runs: 2\n  design:\n    mode: cartesian\n    max_executions: 50\n",
    )
    new = _load(
        tmp_path,
        "  run:\n    n_runs: 2\n  design:\n    plugin: cartesian\n    max_executions: 50\n    params: {}\n",
    )
    old_plan = build_execution_plan(SCENARIOS, ITEMS, old.n_runs, design=old.design.as_plan_dict())
    new_plan = build_execution_plan(SCENARIOS, ITEMS, new.n_runs, design=new.design.as_plan_dict())
    assert _ids(old_plan) == _ids(new_plan)
    assert old.design.plugin == "cartesian"
    assert new.design.plugin == "cartesian"


def test_old_design_mode_warns(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    with caplog.at_level(logging.WARNING, logger="mas.lab.deprecations"):
        yaml = "  design:\n    mode: coupled\n    couplings:\n      - scenario: s1\n        items: [a]\n"
        cfg = _load(tmp_path, yaml)
    assert "experiment.design.mode" in caplog.text
    assert cfg.design.plugin == "coupled"
    assert cfg.design.params["couplings"][0]["scenario"] == "s1"


def test_mixing_plugin_and_mode_is_an_error() -> None:
    with pytest.raises(ValueError, match="not both"):
        ExperimentDesignSpec.from_dict({"plugin": "cartesian", "mode": "cartesian"})


def test_mixing_params_and_top_level_couplings_is_an_error() -> None:
    with pytest.raises(ValueError, match="not both"):
        ExperimentDesignSpec.from_dict(
            {"plugin": "coupled", "couplings": [{"scenario": "s1"}], "params": {"couplings": [{"scenario": "s2"}]}}
        )


def test_new_spelling_does_not_warn(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    with caplog.at_level(logging.WARNING, logger="mas.lab.deprecations"):
        _load(tmp_path, "  design:\n    plugin: cartesian\n")
    assert "experiment.design.mode" not in caplog.text


def test_factorial_selects_declared_scenario_ids() -> None:
    scenarios = ["hierarchical-off", "hierarchical-full", "debate-off", "debate-full"]
    spec = ExperimentDesignSpec.from_dict(
        {
            "plugin": "factorial",
            "params": {
                "factors": {"pattern": ["hierarchical", "debate"], "concord": ["off", "full"]},
                "exclude": ["debate-full"],
            },
        }
    )
    plan = build_execution_plan(scenarios, ITEMS, 1, design=spec.as_plan_dict())
    assert _ids(plan) == [
        ("hierarchical-off", "a", 0),
        ("hierarchical-off", "b", 0),
        ("hierarchical-full", "a", 0),
        ("hierarchical-full", "b", 0),
        ("debate-off", "a", 0),
        ("debate-off", "b", 0),
    ]


def test_factorial_unknown_combination_names_the_scenario() -> None:
    with pytest.raises(ValueError, match=r"unknown scenario: \['missing-run'\]"):
        build_execution_plan(
            ["keep-run"],
            ITEMS,
            1,
            design={
                "plugin": "factorial",
                "params": {"factors": {"a": ["missing"], "b": ["run"]}},
            },
        )


def test_third_party_generator_from_test_library(role_plugins_lib) -> None:
    spec = ExperimentDesignSpec.from_dict({"plugin": "only_scenario", "params": {"scenario": "s2"}})
    plan = build_execution_plan(SCENARIOS, ITEMS, 1, design=spec.as_plan_dict())
    assert _ids(plan) == [("s2", "a", 0), ("s2", "b", 0)]
    with pytest.raises(ValueError, match="params.scenario"):
        ExperimentDesignSpec.from_dict({"plugin": "only_scenario", "params": {}})
    with pytest.raises(ValueError, match=r"unknown scenario generator 'nope'; registered:"):
        ExperimentDesignSpec.from_dict({"plugin": "nope"})
