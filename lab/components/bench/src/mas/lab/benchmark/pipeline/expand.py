#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Plan-based graph expansion (same instance names as v1, no disk scan)."""

from __future__ import annotations

from pathlib import Path

from mas.lab.benchmark.pipeline.keys import RunKey


def planned_nodes(keys: list[RunKey], output_dir: Path) -> dict[str, list]:
    """Build the ``nodes=`` map ``materialize_step_dicts`` expects from a known plan."""
    from mas.lab.benchmark.schedule.pipeline import make_node

    runs = []
    items: dict[tuple[str, str], object] = {}
    scenarios: dict[str, object] = {}
    for key in keys:
        run_path = output_dir / key.scenario / key.item / key.run
        runs.append(make_node("run", run_path, key.scenario, key.item, key.run))
        item_id = (key.scenario, key.item)
        if item_id not in items:
            items[item_id] = make_node("item", output_dir / key.scenario / key.item, key.scenario, key.item)
        if key.scenario not in scenarios:
            scenarios[key.scenario] = make_node("scenario", output_dir / key.scenario, key.scenario)
    experiment = [make_node("experiment", output_dir)]
    return {
        "run": runs,
        "item": list(items.values()),
        "test": list(items.values()),
        "scenario": list(scenarios.values()),
        "experiment": experiment,
        "application": experiment,
    }


def expand_step_dicts(
    specs: list,
    *,
    keys: list[RunKey],
    output_dir: Path,
    phase: str = "post",
    scenario_ids: list[str] | None = None,
    infra_name: str | None = None,
    step_overrides: dict | None = None,
    level_artifacts: dict | None = None,
) -> list[dict]:
    from mas.lab.benchmark.schedule.pipeline import materialize_step_dicts

    nodes = planned_nodes(keys, output_dir)
    return materialize_step_dicts(
        specs,
        phase=phase,
        scenario_ids=list(scenario_ids or sorted({k.scenario for k in keys})),
        infra_name=infra_name,
        step_overrides=step_overrides,
        template_vars={"output_dir": str(output_dir)},
        level_artifacts=level_artifacts,
        nodes=nodes,
    )
