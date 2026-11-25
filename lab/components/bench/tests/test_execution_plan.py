#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Coupled and one-factor execution plans."""

from __future__ import annotations

import pytest
from mas.lab.benchmark.execution.plan import build_execution_plan
from mas.lab.lab.config import ExperimentDesignSpec


def _plan(scenarios, items, n_runs, *, design=None, schedule=None):
    spec = ExperimentDesignSpec.from_dict(design)
    return build_execution_plan(
        scenarios,
        items,
        n_runs,
        design=spec.as_plan_dict(),
        schedule=schedule,
    )


def test_coupled_plan_is_id_pairs_not_cartesian():
    ids = [f"item-{i}" for i in range(25)]
    items = [{"id": i, "inputs": {"user": i}} for i in ids]
    design = {"plugin": "coupled", "params": {"couplings": [{"scenario": i, "items": [i]} for i in ids]}}
    plan = _plan(ids, items, 1, design=design)
    assert len(plan) == 25
    assert [(s, i["id"]) for s, i, _ in plan] == list(zip(ids, ids, strict=False))


def test_coupled_plan_pairs_scenario_to_item_id():
    items = [
        {"id": "celestia-weekend", "inputs": {"user": "A"}},
        {"id": "verdantia-budget", "inputs": {"user": "B"}},
    ]
    design = {
        "plugin": "coupled",
        "params": {
            "couplings": [
                {"scenario": "celestia-weekend", "items": ["celestia-weekend"]},
                {"scenario": "verdantia-budget", "items": ["verdantia-budget"]},
            ]
        },
    }
    plan = _plan(["celestia-weekend", "verdantia-budget"], items, 1, design=design)
    assert [(s, i["id"]) for s, i, _ in plan] == [
        ("celestia-weekend", "celestia-weekend"),
        ("verdantia-budget", "verdantia-budget"),
    ]


@pytest.mark.parametrize(
    "design, scenarios, expected",
    [
        (
            {
                "plugin": "one_factor",
                "params": {
                    "pin_scenarios": ["baseline"],
                    "vary": {"axis": "item", "values": ["item-1"]},
                },
            },
            ["baseline", "treatment"],
            [("baseline", "item-1", 0)],
        ),
        (
            {
                "plugin": "one_factor",
                "params": {
                    "pin_items": ["item-1"],
                    "vary": {"axis": "scenario", "values": ["s1", "s2"]},
                },
            },
            ["s1", "s2", "s3"],
            [("s1", "item-1", 0), ("s2", "item-1", 0)],
        ),
    ],
)
def test_one_factor_plan(design, scenarios, expected):
    spec = ExperimentDesignSpec.from_dict(design)
    plan = build_execution_plan(
        scenarios,
        [{"id": "item-1"}, {"id": "item-2"}],
        1,
        design=spec.as_plan_dict(),
    )
    assert [(s, i["id"], r) for s, i, r in plan] == expected


def test_design_spec_rejects_unread_pin():
    with pytest.raises(ValueError, match="design.pin"):
        ExperimentDesignSpec.from_dict({"pin": {"scenarios": ["s1"]}})


def test_unknown_plugin_lists_registered_ids():
    with pytest.raises(ValueError, match=r"unknown scenario generator 'latin'; registered:"):
        ExperimentDesignSpec.from_dict({"plugin": "latin"})


def test_one_factor_without_vary_raises():
    with pytest.raises(ValueError, match="one_factor"):
        ExperimentDesignSpec.from_dict({"plugin": "one_factor"})


def test_one_factor_unknown_item_raises():
    with pytest.raises(ValueError, match="unknown item"):
        build_execution_plan(
            ["s1"],
            [{"id": "a"}],
            1,
            design={
                "plugin": "one_factor",
                "params": {"vary": {"axis": "item", "values": ["missing"]}},
            },
        )


def test_coupled_without_couplings_raises():
    with pytest.raises(ValueError, match="couplings"):
        ExperimentDesignSpec.from_dict({"plugin": "coupled"})
