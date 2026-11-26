#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Plan an experiment's runs without running anything.

Generation and scheduling only: the same call the batch host makes, so a plan file
names exactly the runs a run of that experiment would execute. A ``pre`` step that
generates the dataset at run time cannot be planned ahead; ``select_runs`` then
refuses the plan with a message saying so.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from mas.lab.benchmark.execution.plan import build_execution_plan, plan_kwargs
from mas.lab.benchmark.execution.roles import PlannedRun


def plan_loaded(
    loaded: Any,
    *,
    scheduler: Optional[str] = None,
    scenario_ids: list | None = None,
    dataset_items: list | None = None,
) -> list[PlannedRun]:
    """Runs of a loaded experiment, generated and ordered."""
    return build_execution_plan(
        scenario_ids if scenario_ids is not None else loaded.scenario_ids,
        dataset_items if dataset_items is not None else loaded.dataset_items,
        loaded.n_runs,
        declared_scenario_ids=list(loaded.declared_scenario_ids) or None,
        **plan_kwargs(loaded.exp, plugin_override=scheduler),
    )


def plan_experiment(
    experiment_yaml: Path,
    *,
    overrides: list[str] | None = None,
    scheduler: Optional[str] = None,
    max_runs: Optional[int] = None,
    scenario_id: Optional[str] = None,
    dataset_item: Optional[str] = None,
    experiment_overlays: list[str] | None = None,
) -> tuple[Any, list[PlannedRun]]:
    """Load *experiment_yaml* and plan it. Returns the loaded experiment and its runs."""
    from mas.lab.benchmark.schedule.run_batch.load import load_experiment

    loaded = load_experiment(
        Path(experiment_yaml),
        overrides=overrides,
        max_runs=max_runs,
        scenario_id=scenario_id,
        dataset_item=dataset_item,
        experiment_overlays=experiment_overlays,
    )
    if loaded is None:
        raise ValueError(f"cannot plan {experiment_yaml}: the experiment did not load (see the log)")
    return loaded, plan_loaded(loaded, scheduler=scheduler)


def scheduler_of(loaded: Any, scheduler: Optional[str] = None) -> str:
    """Scheduler id a plan used: the override, ``schedule.plugin``, or the generator's default."""
    from mas.lab.benchmark.execution.plan import schedule_plan_dict, scheduler_id_for
    from mas.lab.benchmark.execution.roles import resolve_scenario_generator

    generator = resolve_scenario_generator(loaded.exp.design.plugin)
    return scheduler_id_for(generator, schedule_plan_dict(loaded.exp.schedule, plugin_override=scheduler))


def write_plan_for(
    path: Path,
    loaded: Any,
    runs: list[PlannedRun],
    *,
    scheduler: Optional[str] = None,
    attempts_before: dict[str, int] | None = None,
    **extra: Any,
) -> Path:
    """Write *runs* as a run-plan file for the experiment *loaded* names; ``extra`` keys are added."""
    from mas.lab.benchmark.execution.wire import plan_document, write_plan

    document = plan_document(
        runs,
        experiment=str(loaded.exp.name or ""),
        experiment_yaml=str(Path(loaded.experiment_yaml).resolve()),
        generator=loaded.exp.design.plugin,
        scheduler=scheduler_of(loaded, scheduler),
        attempts_before=attempts_before,
    )
    document.update(extra)
    return write_plan(path, document)
