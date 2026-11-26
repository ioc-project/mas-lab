#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Test generator: keep one scenario id."""

from __future__ import annotations

from typing import Any

from mas.lab.benchmark.execution.roles import DesignContext, PlannedRun


class OnlyScenarioGenerator:
    plugin_id = "only_scenario"
    default_scheduler = "as_generated"

    def validate_params(self, params: dict[str, Any]) -> None:
        if "scenario" not in (params or {}):
            raise ValueError("params.scenario is required")
        if not isinstance(params["scenario"], str) or not params["scenario"].strip():
            raise ValueError("params.scenario must be a non-empty string")

    def generate(self, ctx: DesignContext) -> list[PlannedRun]:
        wanted = str(ctx.params["scenario"])
        return [PlannedRun(wanted, item, run_idx) for run_idx in range(ctx.n_runs) for item in ctx.dataset_items]
