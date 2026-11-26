#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Execution plan construction and cartesian guards."""

from __future__ import annotations

from typing import Any

from mas.lab.benchmark.execution.roles import (
    DesignContext,
    PlannedRun,
    ScenarioGenerator,
    ScheduleContext,
    resolve_scenario_generator,
    resolve_scheduler,
)
from mas.lab.lab.config.execution import ExperimentDesignSpec, ExperimentScheduleSpec


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


def design_plan_dict(design: ExperimentDesignSpec | dict | None) -> dict[str, Any]:
    """Normalised design mapping for :func:`build_execution_plan`."""
    if design is None:
        return {}
    if isinstance(design, ExperimentDesignSpec):
        return design.as_plan_dict()
    if isinstance(design, dict):
        return dict(design)
    raise TypeError(f"design must be ExperimentDesignSpec or dict, not {type(design).__name__}")


def schedule_plan_dict(
    schedule: ExperimentScheduleSpec | dict | None,
    *,
    plugin_override: str | None = None,
) -> dict[str, Any]:
    """Normalised schedule mapping for :func:`build_execution_plan`.

    ``plugin_override`` is the CLI ``--scheduler`` value (``--strategy`` alias): it becomes ``schedule.plugin``.
    """
    if isinstance(schedule, ExperimentScheduleSpec):
        data = dict(schedule.as_plan_dict())
    elif isinstance(schedule, dict):
        data = dict(schedule)
    elif schedule is None:
        data = {}
    else:
        raise TypeError(f"schedule must be ExperimentScheduleSpec or dict, not {type(schedule).__name__}")
    if plugin_override:
        data["plugin"] = plugin_override
    return data


def plan_kwargs(exp: Any, *, plugin_override: str | None = None) -> dict[str, Any]:
    """``design`` / ``schedule`` kwargs for :func:`build_execution_plan` from a loaded experiment."""
    return {
        "design": design_plan_dict(exp.design),
        "schedule": schedule_plan_dict(exp.schedule, plugin_override=plugin_override),
    }


def skipped_by_scenario(
    scenario_ids: list,
    dataset_items: list,
    *,
    design: dict | ExperimentDesignSpec | None = None,
) -> int:
    """Count of unique (scenario, item) pairs the items' ``scenarios`` lists leave out of the plan."""
    raw = _generate(scenario_ids, dataset_items, 1, design=design_plan_dict(design))[1]
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
    design: dict | ExperimentDesignSpec | None = None,
    schedule: dict | ExperimentScheduleSpec | None = None,
    declared_scenario_ids: list | None = None,
) -> list[PlannedRun]:
    """Return ordered ``(scenario_id, item, run_idx)`` runs.

    The generator sees ``declared_scenario_ids`` (every scenario the experiment
    declares) so a narrowed run (``--scenario-id``, a shard) never turns a valid
    design into an unknown-scenario error; runs outside ``scenario_ids`` are
    dropped afterwards.
    """
    design_dict = design_plan_dict(design)
    schedule_dict = schedule_plan_dict(schedule)
    generator, plan = _generate(
        scenario_ids,
        dataset_items,
        n_runs,
        design=design_dict,
        declared_scenario_ids=declared_scenario_ids,
    )
    present = {str(sid) for sid in scenario_ids}
    plan = [run for run in plan if run.scenario_id in present and item_applies(run.item, run.scenario_id)]
    enforce_max_executions(len(plan), design_dict)
    scheduler_id = scheduler_id_for(generator, schedule_dict)
    scheduler = resolve_scheduler(scheduler_id)
    params = dict(schedule_dict.get("params") or {})
    scheduler.validate_params(params)
    parallel = schedule_dict.get("parallel_scenarios")
    try:
        cap = max(1, int(parallel)) if parallel is not None else 4
    except (TypeError, ValueError):
        cap = 4
    return scheduler.order(
        plan,
        ScheduleContext(params=params, scenario_ids=list(scenario_ids), parallel_scenarios=cap),
    )


def _generate(
    scenario_ids: list,
    dataset_items: list,
    n_runs: int,
    *,
    design: dict,
    declared_scenario_ids: list | None = None,
) -> tuple[ScenarioGenerator, list[PlannedRun]]:
    plugin_id = str(design.get("plugin") or "cartesian")
    params = dict(design.get("params") or {})
    generator = resolve_scenario_generator(plugin_id)
    generator.validate_params(params)
    ctx = DesignContext(
        scenario_ids=list(declared_scenario_ids or scenario_ids),
        dataset_items=list(dataset_items),
        n_runs=int(n_runs),
        params=params,
    )
    return generator, list(generator.generate(ctx))


def scheduler_id_for(generator: ScenarioGenerator, schedule: dict) -> str:
    explicit = schedule.get("plugin")
    if explicit:
        return str(explicit)
    return generator.default_scheduler or "coverage"


def enforce_max_executions(plan_len: int, design: dict | ExperimentDesignSpec | None = None) -> None:
    design_dict = design_plan_dict(design)
    max_executions = design_dict.get("max_executions")
    if max_executions is None:
        return
    limit = int(max_executions)
    if plan_len > limit:
        raise RuntimeError(
            f"Execution grid {plan_len} exceeds "
            f"design.max_executions={limit}. "
            "Reduce scenarios/items/n_runs or raise the limit."
        )
