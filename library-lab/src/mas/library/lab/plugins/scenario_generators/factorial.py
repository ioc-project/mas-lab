#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Product of named factors, selecting among declared scenario ids.

Scenario ids must already exist as ``<value1>-<value2>-...`` in factor order.
This plugin does not synthesize overlays.
"""

from __future__ import annotations

import itertools
from typing import Any

from mas.lab.benchmark.execution.roles import DesignContext, PlannedRun, reject_unknown_keys


class FactorialGenerator:
    plugin_id = "factorial"
    default_scheduler = "coverage"

    def validate_params(self, params: dict[str, Any]) -> None:
        params = params or {}
        reject_unknown_keys(params, {"factors", "exclude"}, plugin_id=self.plugin_id)
        factors = params.get("factors")
        if not isinstance(factors, dict) or not factors:
            raise ValueError("params.factors must be a non-empty mapping of name to value lists")
        for name, values in factors.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("params.factors keys must be non-empty strings")
            if not isinstance(values, list) or not values:
                raise ValueError(f"params.factors[{name!r}] must be a non-empty list")
        exclude = params.get("exclude") or []
        if not isinstance(exclude, list):
            raise ValueError("params.exclude must be a list")

    def generate(self, ctx: DesignContext) -> list[PlannedRun]:
        factors: dict[str, list] = ctx.params.get("factors") or {}
        names = list(factors)
        value_lists = [list(factors[name]) for name in names]
        exclude = {self._run_id(names, row) for row in (ctx.params.get("exclude") or [])}
        known = {str(s) for s in ctx.scenario_ids}
        selected: list[str] = []
        for combo in itertools.product(*value_lists):
            scenario_id = "-".join(str(v) for v in combo)
            if scenario_id in exclude:
                continue
            if scenario_id not in known:
                raise ValueError(f"design references unknown scenario: {[scenario_id]}")
            selected.append(scenario_id)
        return [
            PlannedRun(scenario_id, item, run_idx)
            for scenario_id in selected
            for item in ctx.dataset_items
            for run_idx in range(ctx.n_runs)
        ]

    @staticmethod
    def _run_id(names: list[str], row: Any) -> str:
        if isinstance(row, str):
            return row
        if not isinstance(row, dict):
            raise ValueError("params.exclude entries must be scenario ids or factor maps")
        return "-".join(str(row[name]) for name in names)
