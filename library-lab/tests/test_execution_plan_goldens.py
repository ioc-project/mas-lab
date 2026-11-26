#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Golden execution plans recorded on the pre-plugin builders.

Expected triples are the lock: a later generator or scheduler plugin
must emit the same runs in the same order for these inputs.
"""

from __future__ import annotations

import pytest
from mas.lab.benchmark.execution.plan import build_execution_plan, skipped_by_scenario
from mas.lab.lab.config import ExperimentDesignSpec


def _design(data: dict | None) -> dict:
    return ExperimentDesignSpec.from_dict(data).as_plan_dict()


def _plan(scenarios, items, n_runs, *, design=None, schedule=None):
    return build_execution_plan(
        scenarios,
        items,
        n_runs,
        design=_design(design) if design is not None else None,
        schedule=schedule,
    )


SCENARIOS = ["s1", "s2"]
ITEMS = [{"id": "a"}, {"id": "b"}]


def _ids(plan) -> list[tuple[str, str, int]]:
    return [(sid, item["id"], run_idx) for sid, item, run_idx in plan]


def test_golden_cartesian_n1_default_order() -> None:
    assert _ids(_plan(SCENARIOS, ITEMS, 1)) == [
        ("s1", "a", 0),
        ("s1", "b", 0),
        ("s2", "a", 0),
        ("s2", "b", 0),
    ]


def test_golden_cartesian_n2_coverage() -> None:
    assert _ids(_plan(SCENARIOS, ITEMS, 2)) == [
        ("s1", "a", 0),
        ("s1", "b", 0),
        ("s2", "a", 0),
        ("s2", "b", 0),
        ("s1", "a", 1),
        ("s1", "b", 1),
        ("s2", "a", 1),
        ("s2", "b", 1),
    ]


def test_golden_cartesian_n2_depth() -> None:
    assert _ids(_plan(SCENARIOS, ITEMS, 2, schedule={"plugin": "depth"})) == [
        ("s1", "a", 0),
        ("s1", "a", 1),
        ("s1", "b", 0),
        ("s1", "b", 1),
        ("s2", "a", 0),
        ("s2", "a", 1),
        ("s2", "b", 0),
        ("s2", "b", 1),
    ]


def test_golden_cartesian_item_filter_coverage() -> None:
    items = [{"id": "a"}, {"id": "b", "scenarios": ["s1"]}]
    plan = _plan(SCENARIOS, items, 2)
    assert _ids(plan) == [
        ("s1", "a", 0),
        ("s1", "b", 0),
        ("s2", "a", 0),
        ("s1", "a", 1),
        ("s1", "b", 1),
        ("s2", "a", 1),
    ]
    assert skipped_by_scenario(SCENARIOS, items) == 1


def test_golden_cartesian_item_filter_depth() -> None:
    items = [{"id": "a"}, {"id": "b", "scenarios": ["s1"]}]
    assert _ids(_plan(SCENARIOS, items, 2, schedule={"plugin": "depth"})) == [
        ("s1", "a", 0),
        ("s1", "a", 1),
        ("s1", "b", 0),
        ("s1", "b", 1),
        ("s2", "a", 0),
        ("s2", "a", 1),
    ]


def test_golden_coupled_n1_and_n2_ignore_ordering() -> None:
    design = {
        "mode": "coupled",
        "couplings": [
            {"scenario": "s1", "items": ["a"]},
            {"scenario": "s2", "items": ["b"]},
        ],
    }
    assert _ids(_plan(SCENARIOS, ITEMS, 1, design=design)) == [
        ("s1", "a", 0),
        ("s2", "b", 0),
    ]
    assert _ids(_plan(SCENARIOS, ITEMS, 2, design=design)) == [
        ("s1", "a", 0),
        ("s1", "a", 1),
        ("s2", "b", 0),
        ("s2", "b", 1),
    ]


def test_golden_coupled_omitted_items_means_every_item() -> None:
    design = {"mode": "coupled", "couplings": [{"scenario": "s1"}]}
    assert _ids(_plan(SCENARIOS, ITEMS, 1, design=design)) == [
        ("s1", "a", 0),
        ("s1", "b", 0),
    ]


def test_golden_one_factor_vary_item_n2_ignores_ordering() -> None:
    design = {
        "mode": "one_factor",
        "pin_scenarios": ["s1"],
        "vary": {"axis": "item", "values": ["a", "b"]},
    }
    assert _ids(_plan(SCENARIOS, ITEMS, 2, design=design)) == [
        ("s1", "a", 0),
        ("s1", "a", 1),
        ("s1", "b", 0),
        ("s1", "b", 1),
    ]


def test_golden_one_factor_vary_scenario_n2() -> None:
    design = {
        "mode": "one_factor",
        "pin_items": ["a"],
        "vary": {"axis": "scenario", "values": ["s1", "s2"]},
    }
    assert _ids(_plan(["s1", "s2", "s3"], ITEMS, 2, design=design)) == [
        ("s1", "a", 0),
        ("s1", "a", 1),
        ("s2", "a", 0),
        ("s2", "a", 1),
    ]


def test_golden_max_executions_failure_message() -> None:
    with pytest.raises(
        RuntimeError,
        match=r"Execution grid 12 exceeds design.max_executions=2\. Reduce scenarios/items/n_runs or raise the limit\.",
    ):
        _plan(
            SCENARIOS,
            ITEMS,
            3,
            design={"mode": "cartesian", "max_executions": 2},
        )


def test_golden_unknown_mode_message() -> None:
    with pytest.raises(ValueError, match=r"unknown scenario generator 'latin'; registered:"):
        ExperimentDesignSpec.from_dict({"mode": "latin"})


def test_golden_one_factor_without_vary_message() -> None:
    with pytest.raises(
        ValueError,
        match=r"design.mode is one_factor; design.vary must set axis \(scenario\|item\) and a non-empty values list",
    ):
        ExperimentDesignSpec.from_dict({"mode": "one_factor"})


def test_golden_one_factor_unknown_item_message() -> None:
    with pytest.raises(ValueError, match=r"design references unknown item: \['missing'\]"):
        _plan(
            ["s1"],
            [{"id": "a"}],
            1,
            design={"mode": "one_factor", "vary": {"axis": "item", "values": ["missing"]}},
        )


def test_golden_coupled_without_couplings_message() -> None:
    with pytest.raises(ValueError, match=r"design.mode is coupled but design.couplings is missing or empty"):
        ExperimentDesignSpec.from_dict({"mode": "coupled"})


def test_golden_coupled_missing_scenario_message() -> None:
    with pytest.raises(ValueError, match=r"design.couplings entry is missing scenario"):
        _plan(
            ["s1"],
            [{"id": "a"}],
            1,
            design={"mode": "coupled", "couplings": [{}]},
        )


def test_golden_one_factor_unknown_scenario_message() -> None:
    with pytest.raises(ValueError, match=r"design references unknown scenario: \['s9'\]"):
        _plan(
            ["s1"],
            [{"id": "a"}],
            1,
            design={
                "mode": "one_factor",
                "vary": {"axis": "scenario", "values": ["s9"]},
                "pin_scenarios": ["s1"],
            },
        )


def test_golden_unread_pin_message() -> None:
    with pytest.raises(ValueError, match=r"design.pin is not read; use pin_scenarios / pin_items with one_factor"):
        ExperimentDesignSpec.from_dict({"pin": {"scenarios": ["s1"]}})


def test_golden_empty_item_scenarios_message() -> None:
    with pytest.raises(ValueError, match=r"item 'a' scenarios is empty: omit it to run in every scenario"):
        build_execution_plan(["s1"], [{"id": "a", "scenarios": []}], 1)


def test_golden_mapping_item_scenarios_message() -> None:
    with pytest.raises(ValueError, match=r"item 'a' scenarios must be a list of ids, not a mapping"):
        build_execution_plan(["s1"], [{"id": "a", "scenarios": {"x": 1}}], 1)


def test_golden_plugin_spelling_matches_mode_cartesian() -> None:
    via_mode = _ids(_plan(SCENARIOS, ITEMS, 2, design={"mode": "cartesian"}, schedule={"plugin": "depth"}))
    via_plugin = _ids(
        _plan(SCENARIOS, ITEMS, 2, design={"plugin": "cartesian", "params": {}}, schedule={"plugin": "depth"})
    )
    assert (
        via_mode
        == via_plugin
        == [
            ("s1", "a", 0),
            ("s1", "a", 1),
            ("s1", "b", 0),
            ("s1", "b", 1),
            ("s2", "a", 0),
            ("s2", "a", 1),
            ("s2", "b", 0),
            ("s2", "b", 1),
        ]
    )


def test_golden_plugin_spelling_matches_mode_coupled() -> None:
    old = {
        "mode": "coupled",
        "couplings": [{"scenario": "s1", "items": ["a"]}, {"scenario": "s2", "items": ["b"]}],
    }
    new = {
        "plugin": "coupled",
        "params": {"couplings": [{"scenario": "s1", "items": ["a"]}, {"scenario": "s2", "items": ["b"]}]},
    }
    assert _ids(_plan(SCENARIOS, ITEMS, 2, design=old)) == _ids(_plan(SCENARIOS, ITEMS, 2, design=new))
