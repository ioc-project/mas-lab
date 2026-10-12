#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Coupled and one-factor execution plans."""
from __future__ import annotations

import pytest

from mas.lab.benchmark.execution.plan import (
    _build_coupled_plan,
    build_execution_plan,
)
from mas.lab.lab.config import ExperimentDesignSpec


def test_coupled_plan_is_id_pairs_not_cartesian():
    ids = [f"item-{i}" for i in range(25)]
    items = [{"id": i, "inputs": {"user": i}} for i in ids]
    couplings = [{"scenario": i, "items": [i]} for i in ids]
    plan = _build_coupled_plan(couplings, items, n_runs=1)
    assert len(plan) == 25
    assert [(s, i["id"]) for s, i, _ in plan] == list(zip(ids, ids))


def test_coupled_plan_pairs_scenario_to_item_id():
    items = [
        {"id": "celestia-weekend", "inputs": {"user": "A"}},
        {"id": "verdantia-budget", "inputs": {"user": "B"}},
    ]
    couplings = [
        {"scenario": "celestia-weekend", "items": ["celestia-weekend"]},
        {"scenario": "verdantia-budget", "items": ["verdantia-budget"]},
    ]
    plan = _build_coupled_plan(couplings, items, n_runs=1)
    assert [(s, i["id"]) for s, i, _ in plan] == [
        ("celestia-weekend", "celestia-weekend"),
        ("verdantia-budget", "verdantia-budget"),
    ]


@pytest.mark.parametrize(
    "design, scenarios, expected",
    [
        (
            {
                "mode": "one_factor",
                "pin_scenarios": ["baseline"],
                "vary": {"axis": "item", "values": ["item-1"]},
            },
            ["baseline", "treatment"],
            [("baseline", "item-1", 0)],
        ),
        (
            {
                "mode": "one_factor",
                "pin_items": ["item-1"],
                "vary": {"axis": "scenario", "values": ["s1", "s2"]},
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


def test_unknown_mode_raises():
    with pytest.raises(ValueError, match="design.mode"):
        build_execution_plan(["s1"], [{"id": "a"}], 1, design={"mode": "latin"})


def test_one_factor_without_vary_raises():
    with pytest.raises(ValueError, match="one_factor"):
        build_execution_plan(
            ["s1"], [{"id": "a"}], 1, design={"mode": "one_factor"}
        )


def test_one_factor_unknown_item_raises():
    with pytest.raises(ValueError, match="unknown item"):
        build_execution_plan(
            ["s1"],
            [{"id": "a"}],
            1,
            design={
                "mode": "one_factor",
                "vary": {"axis": "item", "values": ["missing"]},
            },
        )


def test_coupled_without_couplings_raises():
    with pytest.raises(ValueError, match="couplings"):
        build_execution_plan(
            ["s1"], [{"id": "a"}], 1, design={"mode": "coupled"}
        )
