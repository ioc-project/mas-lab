#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Execution plan construction and cartesian guards."""

from __future__ import annotations

_MODES = frozenset({"cartesian", "coupled", "one_factor"})


def _declared_scenarios(item: dict) -> set[str] | None:
    """The scenario ids on *item*, or ``None`` when the field is omitted (every scenario).

    A lone string is one id. An empty list means no scenario. A mapping is an error.
    """
    declared = item.get("scenarios")
    if declared is None:
        return None
    if isinstance(declared, str):
        token = declared.strip()
        if not token:
            raise ValueError(f"item {item.get('id')!r} scenarios is empty: omit it to run in every scenario")
        return {token}
    if isinstance(declared, bool):
        raise ValueError(f"item {item.get('id')!r} scenarios must be a list of ids, not a boolean")
    if isinstance(declared, dict):
        raise ValueError(f"item {item.get('id')!r} scenarios must be a list of ids, not a mapping")
    if isinstance(declared, (int, float)):
        raise ValueError(f"item {item.get('id')!r} scenarios must be a list of ids, not a number")
    try:
        entries = list(declared)
    except TypeError:
        raise ValueError(f"item {item.get('id')!r} scenarios must be a list of ids") from None
    if not entries:
        raise ValueError(f"item {item.get('id')!r} scenarios is empty: omit it to run in every scenario")
    out: set[str] = set()
    for s in entries:
        if not isinstance(s, str):
            raise ValueError(f"item {item.get('id')!r} scenarios entries must be non-empty strings")
        token = s.strip()
        if not token:
            raise ValueError(f"item {item.get('id')!r} scenarios entries must be non-empty strings")
        out.add(token)
    return out


def check_item_scenarios(scenario_ids: list, dataset_items: list) -> None:
    """Refuse an item whose ``scenarios`` names an id the experiment does not declare.

    A misspelt id would otherwise drop the item from every scenario without a word.
    """
    known = {str(s).strip() for s in scenario_ids}
    problems: list[str] = []
    for item in dataset_items:
        declared = _declared_scenarios(item)
        unknown = sorted(declared - known) if declared else []
        if unknown:
            problems.append(f"item {item.get('id')!r} names unknown scenario(s) {unknown}")
    if problems:
        raise ValueError("; ".join(problems) + f"; the experiment declares {sorted(known)}")


def item_applies(item: dict, scenario_id: str) -> bool:
    """True when *item* runs in *scenario_id*: no ``scenarios`` list on the item means every scenario."""
    declared = _declared_scenarios(item)
    return declared is None or str(scenario_id).strip() in declared


def skipped_by_scenario(
    scenario_ids: list,
    dataset_items: list,
    *,
    execution: dict | None = None,
    strategy: str = "coverage",
    design: dict | None = None,
) -> int:
    """Count of unique (scenario, item) pairs the items' ``scenarios`` lists leave out of the plan."""
    raw = _raw_plan(
        scenario_ids,
        dataset_items,
        1,
        execution=execution,
        strategy=strategy,
        design=design,
    )
    seen: set[tuple[str, int]] = set()
    skipped = 0
    for sid, item, _ in raw:
        key = (str(sid), id(item))
        if key in seen:
            continue
        seen.add(key)
        if not item_applies(item, sid):
            skipped += 1
    return skipped


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
    plan = _raw_plan(
        scenario_ids,
        dataset_items,
        n_runs,
        execution=execution,
        strategy=strategy,
        design=design,
    )
    plan = [(sid, item, run_idx) for sid, item, run_idx in plan if item_applies(item, sid)]
    execution = execution or {}
    design = design or execution.get("design") or {}
    enforce_max_executions(len(plan), execution, design=design)
    return plan


def _raw_plan(
    scenario_ids: list,
    dataset_items: list,
    n_runs: int,
    *,
    execution: dict | None = None,
    strategy: str = "coverage",
    design: dict | None = None,
) -> list[tuple[str, dict, int]]:
    execution = execution or {}
    design = design or execution.get("design") or {}
    mode = design.get("mode", "cartesian")
    if mode not in _MODES:
        raise ValueError(f"design.mode {mode!r} is not one of {sorted(_MODES)}")
    if mode == "coupled":
        return _build_coupled_plan(design.get("couplings"), dataset_items, n_runs)
    if mode == "one_factor":
        return _build_one_factor_plan(design, scenario_ids, dataset_items, n_runs)
    return _build_cartesian_plan(scenario_ids, dataset_items, n_runs, strategy)


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
            selected = [items_by_id[iid] for iid in _require_ids(item_ids, set(items_by_id), kind="item")]
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
            "design.mode is one_factor; design.vary must set axis (scenario|item) and a non-empty values list"
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
        base_items = [items_by_id[iid] for iid in _require_ids(pinned_items, set(items_by_id), kind="item")]
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
