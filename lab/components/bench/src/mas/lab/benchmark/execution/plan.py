#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Execution plan construction and cartesian guards."""

_MODES = frozenset({"cartesian", "coupled", "one_factor"})


def build_execution_plan(
    scenario_ids: list,
    dataset_items: list,
    n_runs: int,
    *,
    execution: dict | None = None,
    strategy: str = "coverage",
    design: dict | None = None,
) -> list[tuple[str, dict, int]]:
    """Return ordered ``(scenario_id, item, run_idx)`` triples."""
    execution = execution or {}
    design = design or execution.get("design") or {}
    mode = design.get("mode", "cartesian")
    if mode not in _MODES:
        raise ValueError(
            f"design.mode {mode!r} is not one of {sorted(_MODES)}"
        )

    if mode == "coupled":
        plan = _build_coupled_plan(design.get("couplings"), dataset_items, n_runs)
    elif mode == "one_factor":
        plan = _build_one_factor_plan(
            design,
            scenario_ids,
            dataset_items,
            n_runs,
        )
    else:
        plan = _build_cartesian_plan(scenario_ids, dataset_items, n_runs, strategy)

    enforce_max_executions(len(plan), execution, design=design)
    return plan


def _build_cartesian_plan(
    scenario_ids: list,
    dataset_items: list,
    n_runs: int,
    strategy: str,
) -> list[tuple[str, dict, int]]:
    if strategy == "depth":
        return [
            (scenario_id, item, run_idx)
            for scenario_id in scenario_ids
            for item in dataset_items
            for run_idx in range(n_runs)
        ]
    return [
        (scenario_id, item, run_idx)
        for run_idx in range(n_runs)
        for scenario_id in scenario_ids
        for item in dataset_items
    ]


def _items_by_id(dataset_items: list) -> dict:
    return {str(i.get("id")): i for i in dataset_items}


def _require_ids(requested: list, available: set[str], *, kind: str) -> list[str]:
    ids = [str(x) for x in requested]
    missing = [i for i in ids if i not in available]
    if missing:
        raise ValueError(f"design references unknown {kind}: {missing}")
    return ids


def _build_coupled_plan(
    couplings: list[dict] | None,
    dataset_items: list,
    n_runs: int,
) -> list[tuple[str, dict, int]]:
    if not couplings:
        raise ValueError("design.mode is coupled but design.couplings is missing or empty")
    items_by_id = _items_by_id(dataset_items)
    plan: list[tuple[str, dict, int]] = []
    for pair in couplings:
        scenario_id = pair.get("scenario")
        if not scenario_id:
            raise ValueError("design.couplings entry is missing scenario")
        item_ids = pair.get("items")
        if item_ids is None:
            selected = dataset_items
        else:
            selected = [
                items_by_id[iid]
                for iid in _require_ids(item_ids, set(items_by_id), kind="item")
            ]
        for run_idx in range(n_runs):
            for item in selected:
                plan.append((scenario_id, item, run_idx))
    return plan


def _build_one_factor_plan(
    design: dict,
    scenario_ids: list,
    dataset_items: list,
    n_runs: int,
) -> list[tuple[str, dict, int]]:
    """Vary one axis in ``design.vary``; pin others with pin_scenarios / pin_items."""
    vary = design.get("vary") or {}
    axis = vary.get("axis")
    values = vary.get("values") or []
    if axis not in {"scenario", "item"} or not values:
        raise ValueError(
            "design.mode is one_factor; design.vary must set axis "
            "(scenario|item) and a non-empty values list"
        )

    scenario_set = {str(s) for s in scenario_ids}
    items_by_id = _items_by_id(dataset_items)
    pinned_scenarios = _require_ids(
        design.get("pin_scenarios") or scenario_ids,
        scenario_set,
        kind="scenario",
    )
    pinned_items = design.get("pin_items")
    if pinned_items is not None:
        base_items = [
            items_by_id[iid]
            for iid in _require_ids(pinned_items, set(items_by_id), kind="item")
        ]
    else:
        base_items = dataset_items

    plan: list[tuple[str, dict, int]] = []
    if axis == "scenario":
        for scenario_id in _require_ids(values, set(pinned_scenarios), kind="scenario"):
            for run_idx in range(n_runs):
                for item in base_items:
                    plan.append((scenario_id, item, run_idx))
    else:
        for item_id in _require_ids(values, set(items_by_id), kind="item"):
            item = items_by_id[item_id]
            for scenario_id in pinned_scenarios:
                for run_idx in range(n_runs):
                    plan.append((scenario_id, item, run_idx))
    return plan


def enforce_max_executions(
    plan_len: int,
    execution: dict | None = None,
    *,
    design: dict | None = None,
) -> None:
    if design is None:
        if not execution:
            return
        design = execution.get("design")
    if not isinstance(design, dict):
        return
    max_executions = design.get("max_executions")
    if max_executions is None:
        return
    limit = int(max_executions)
    if plan_len > limit:
        raise RuntimeError(
            f"Execution grid {plan_len} exceeds "
            f"design.max_executions={limit}. "
            "Reduce scenarios/items/n_runs or raise the limit."
        )
