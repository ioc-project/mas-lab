#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A dataset item can declare the scenarios it applies to; the planner skips the others."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from mas.lab.benchmark.dataset import Dataset
from mas.lab.benchmark.execution.plan import build_execution_plan, skipped_by_scenario
from mas.lab.benchmark.schedule.run_batch.load import print_dry_run
from mas.lab.lab.config.execution import ExperimentExecutorSpec, ExperimentScheduleSpec

SCENARIOS = ["bare", "guarded"]


def _items(control_scenarios=None):
    control = {"id": "control", "inputs": {"user": "c"}}
    if control_scenarios is not None:
        control["scenarios"] = control_scenarios
    return [{"id": "a", "inputs": {"user": "a"}}, control]


def test_plan_without_the_field_runs_every_pair():
    plan = build_execution_plan(SCENARIOS, _items(), 1)
    assert len(plan) == 4
    assert skipped_by_scenario(SCENARIOS, _items()) == 0


def test_plan_with_the_field_skips_the_other_scenarios():
    items = _items(["bare"])
    plan = build_execution_plan(SCENARIOS, items, 2)
    pairs = {(sid, item["id"]) for sid, item, _ in plan}
    assert pairs == {("bare", "a"), ("guarded", "a"), ("bare", "control")}
    assert len(plan) == 6  # a skipped item never gets a run, so never a run folder
    assert skipped_by_scenario(SCENARIOS, items) == 1


def test_field_reaches_the_planner_from_a_dataset_file(tmp_path: Path):
    path = tmp_path / "dataset.yaml"
    path.write_text(
        "spec:\n  items:\n    - id: control\n      inputs: {user: c}\n      scenarios: [bare]\n",
        encoding="utf-8",
    )
    item = Dataset.from_yaml(path).items[0].to_dict()
    assert item["scenarios"] == ["bare"]
    assert [sid for sid, _, _ in build_execution_plan(SCENARIOS, [item], 1)] == ["bare"]


def test_a_string_scenarios_value_is_one_id():
    items = _items("bare")
    plan = build_execution_plan(SCENARIOS, items, 1)
    pairs = {(sid, item["id"]) for sid, item, _ in plan}
    assert pairs == {("bare", "a"), ("guarded", "a"), ("bare", "control")}


def test_a_mapping_scenarios_value_is_an_error():
    items = _items({"bare": True})
    with pytest.raises(ValueError, match="not a mapping"):
        build_execution_plan(SCENARIOS, items, 1)


def test_a_boolean_scenarios_value_is_an_error():
    items = _items(True)
    with pytest.raises(ValueError, match="not a boolean"):
        build_execution_plan(SCENARIOS, items, 1)


def test_a_numeric_scenarios_value_is_an_error():
    items = _items(1)
    with pytest.raises(ValueError, match="not a number"):
        build_execution_plan(SCENARIOS, items, 1)


def test_a_padded_scenarios_id_still_matches():
    items = _items(["  bare  "])
    plan = build_execution_plan(SCENARIOS, items, 1)
    pairs = {(sid, item["id"]) for sid, item, _ in plan}
    assert pairs == {("bare", "a"), ("guarded", "a"), ("bare", "control")}


def test_a_non_string_scenarios_entry_is_an_error():
    items = _items([1])
    with pytest.raises(ValueError, match="non-empty strings"):
        build_execution_plan(SCENARIOS, items, 1)


def test_coupled_mode_honors_the_field():
    items = _items(["bare"])
    design = {
        "plugin": "coupled",
        "params": {"couplings": [{"scenario": "guarded", "items": ["control"]}]},
    }
    plan = build_execution_plan(SCENARIOS, items, 1, design=design)
    assert plan == []
    assert skipped_by_scenario(SCENARIOS, items, design=design) == 1


def test_dry_run_reports_the_skip_count(capsys):
    loaded = SimpleNamespace(
        exp=SimpleNamespace(name="e", mas=None, design=None, schedule=None, executor=ExperimentExecutorSpec()),
        configs_dir=None,
        scenario_ids=SCENARIOS,
        dataset_items=_items(["bare"]),
        n_runs=1,
        pipeline_specs=[],
    )
    print_dry_run(loaded)
    out = capsys.readouterr().out
    assert "1 items skipped by scenario" in out and "Total      : 3 executions" in out
    assert "Scheduler  : default" in out
    assert "Executor   : native" in out


def test_dry_run_prints_schedule_plugin_and_honours_scheduler(capsys):
    loaded = SimpleNamespace(
        exp=SimpleNamespace(
            name="e",
            mas=None,
            design=None,
            schedule=ExperimentScheduleSpec(plugin="coverage"),
            executor=ExperimentExecutorSpec(),
        ),
        configs_dir=None,
        scenario_ids=SCENARIOS,
        dataset_items=_items(),
        n_runs=1,
        pipeline_specs=[],
    )
    print_dry_run(loaded)
    assert "Scheduler  : coverage" in capsys.readouterr().out
    print_dry_run(loaded, scheduler="depth")
    assert "Scheduler  : depth" in capsys.readouterr().out


def test_dry_run_coupled_total_follows_the_named_pairs(capsys):
    loaded = SimpleNamespace(
        exp=SimpleNamespace(
            name="e",
            mas=None,
            schedule=None,
            executor=ExperimentExecutorSpec(),
            design={
                "plugin": "coupled",
                "params": {"couplings": [{"scenario": "guarded", "items": ["control"]}]},
            },
        ),
        configs_dir=None,
        scenario_ids=SCENARIOS,
        dataset_items=_items(["guarded"]),
        n_runs=1,
        pipeline_specs=[],
    )
    print_dry_run(loaded)
    out = capsys.readouterr().out
    assert "0 items skipped by scenario" in out and "Total      : 1 executions" in out


def test_an_unknown_scenario_id_is_refused_not_silently_dropped():
    from mas.lab.benchmark.execution.plan import check_item_scenarios

    with pytest.raises(ValueError, match="basline"):
        check_item_scenarios(["baseline", "guarded"], [{"id": "a", "scenarios": ["basline"]}])
    check_item_scenarios(["baseline"], [{"id": "a", "scenarios": "baseline"}, {"id": "b"}])


def test_an_empty_scenarios_list_is_refused():
    with pytest.raises(ValueError, match="empty"):
        build_execution_plan(["baseline"], [{"id": "a", "scenarios": []}], 1)


def test_dry_run_reports_a_refused_manifest_instead_of_failing(tmp_path: Path, capsys):
    from mas.ctl.benchmark.runner_dispatch import UnrecognisedManifestError  # noqa: F401

    (tmp_path / "mas.yaml").write_text("kind: mas\n", encoding="utf-8")
    (tmp_path / "app.yaml").write_text("kind: Applicaton\n", encoding="utf-8")
    loaded = SimpleNamespace(
        exp=SimpleNamespace(
            name="x",
            mas=SimpleNamespace(manifest=tmp_path / "app.yaml"),
            design=None,
            schedule=None,
            executor=ExperimentExecutorSpec(),
        ),
        configs_dir=None,
        scenario_ids=["a"],
        dataset_items=[{"id": "i"}],
        n_runs=1,
        pipeline_specs=[],
    )
    print_dry_run(loaded)
    assert "refused" in capsys.readouterr().out


def test_a_blank_scenarios_string_is_refused():
    from mas.lab.benchmark.execution.plan import check_item_scenarios

    with pytest.raises(ValueError, match="empty"):
        check_item_scenarios(["s1"], [{"id": "a", "scenarios": " "}])


def test_dry_run_survives_an_invalid_design(tmp_path: Path, capsys):
    loaded = SimpleNamespace(
        exp=SimpleNamespace(
            name="x",
            mas=None,
            design={"plugin": "bogus"},
            schedule=None,
            executor=ExperimentExecutorSpec(),
        ),
        configs_dir=None,
        scenario_ids=["a"],
        dataset_items=[{"id": "i"}],
        n_runs=1,
        pipeline_specs=[],
    )
    print_dry_run(loaded)
    assert "refused" in capsys.readouterr().out


def test_dry_run_survives_a_max_executions_guard(capsys):
    loaded = SimpleNamespace(
        exp=SimpleNamespace(
            name="x",
            mas=None,
            design={"plugin": "cartesian", "max_executions": 1},
            schedule=None,
            executor=ExperimentExecutorSpec(),
        ),
        configs_dir=None,
        scenario_ids=["a", "b"],
        dataset_items=[{"id": "i"}],
        n_runs=1,
        pipeline_specs=[],
    )
    print_dry_run(loaded)
    assert "refused" in capsys.readouterr().out


def test_a_narrowed_run_still_accepts_items_for_a_scenario_it_did_not_select():
    from mas.lab.benchmark.execution.plan import check_item_scenarios

    declared = ["A", "B"]
    check_item_scenarios(declared, [{"id": "i", "scenarios": ["B"]}])
    assert build_execution_plan(["A"], [{"id": "i", "scenarios": ["B"]}], 1) == []
