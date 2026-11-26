#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Listed (scenario, items) pairs only."""

from __future__ import annotations

from typing import Any

from mas.lab.benchmark.execution.roles import DesignContext, PlannedRun, reject_unknown_keys

from ._util import items_by_id, require_ids


class CoupledGenerator:
    plugin_id = "coupled"
    default_scheduler = "as_generated"

    def validate_params(self, params: dict[str, Any]) -> None:
        params = params or {}
        reject_unknown_keys(params, {"couplings"}, plugin_id=self.plugin_id)
        couplings = params.get("couplings")
        if not couplings:
            raise ValueError("design.mode is coupled but design.couplings is missing or empty")

    def generate(self, ctx: DesignContext) -> list[PlannedRun]:
        lookup = items_by_id(ctx.dataset_items)
        plan: list[PlannedRun] = []
        for pair in ctx.params["couplings"]:
            scenario_id = pair.get("scenario")
            if not scenario_id:
                raise ValueError("design.couplings entry is missing scenario")
            item_ids = pair.get("items")
            if item_ids is None:
                selected = ctx.dataset_items
            else:
                selected = [lookup[iid] for iid in require_ids(item_ids, set(lookup), kind="item")]
            for run_idx in range(ctx.n_runs):
                for item in selected:
                    plan.append(PlannedRun(str(scenario_id), item, run_idx))
        return plan
