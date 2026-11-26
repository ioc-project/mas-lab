#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Cartesian product of scenarios × items × runs."""

from __future__ import annotations

from mas.lab.benchmark.execution.roles import DesignContext, NoParams, PlannedRun


class CartesianGenerator(NoParams):
    plugin_id = "cartesian"
    default_scheduler = "coverage"

    def generate(self, ctx: DesignContext) -> list[PlannedRun]:
        return [
            PlannedRun(scenario_id, item, run_idx)
            for scenario_id in ctx.scenario_ids
            for item in ctx.dataset_items
            for run_idx in range(ctx.n_runs)
        ]
