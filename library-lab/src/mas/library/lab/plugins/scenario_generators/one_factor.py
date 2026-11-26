#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Sweep one of the scenario or item axes; pin the other."""

from __future__ import annotations

from typing import Any

from mas.lab.benchmark.execution.roles import DesignContext, PlannedRun, reject_unknown_keys

from ._util import items_by_id, require_ids


class OneFactorGenerator:
    plugin_id = "one_factor"
    default_scheduler = "as_generated"

    def validate_params(self, params: dict[str, Any]) -> None:
        params = params or {}
        reject_unknown_keys(params, {"vary", "pin_scenarios", "pin_items"}, plugin_id=self.plugin_id)
        vary = params.get("vary") or {}
        axis = vary.get("axis") if isinstance(vary, dict) else None
        values = (vary.get("values") or []) if isinstance(vary, dict) else []
        if axis not in {"scenario", "item"} or not values:
            raise ValueError(
                "design.mode is one_factor; design.vary must set axis (scenario|item) and a non-empty values list"
            )

    def generate(self, ctx: DesignContext) -> list[PlannedRun]:
        vary = ctx.params["vary"]
        axis, values = vary["axis"], vary["values"]

        scenario_set = {str(s) for s in ctx.scenario_ids}
        lookup = items_by_id(ctx.dataset_items)
        pinned_scenarios = require_ids(
            ctx.params.get("pin_scenarios") or ctx.scenario_ids,
            scenario_set,
            kind="scenario",
        )
        pinned_items = ctx.params.get("pin_items")
        if pinned_items is not None:
            base_items = [lookup[iid] for iid in require_ids(pinned_items, set(lookup), kind="item")]
        else:
            base_items = ctx.dataset_items

        plan: list[PlannedRun] = []
        if axis == "scenario":
            for scenario_id in require_ids(values, set(pinned_scenarios), kind="scenario"):
                for run_idx in range(ctx.n_runs):
                    for item in base_items:
                        plan.append(PlannedRun(scenario_id, item, run_idx))
        else:
            for item_id in require_ids(values, set(lookup), kind="item"):
                item = lookup[item_id]
                for scenario_id in pinned_scenarios:
                    for run_idx in range(ctx.n_runs):
                        plan.append(PlannedRun(scenario_id, item, run_idx))
        return plan
