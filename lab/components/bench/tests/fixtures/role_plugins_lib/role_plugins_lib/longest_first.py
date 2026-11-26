#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Test scheduler: longest scenario id first, then original order."""

from __future__ import annotations

from typing import Any

from mas.lab.benchmark.execution.roles import PlannedRun, ScheduleContext


class LongestFirstScheduler:
    plugin_id = "longest_first"

    def validate_params(self, params: dict[str, Any]) -> None:
        extra = sorted(set(params or {}) - {"prefer"})
        if extra:
            raise ValueError(f"params.{extra[0]} is not a longest_first key")

    def order(self, runs: list[PlannedRun], ctx: ScheduleContext) -> list[PlannedRun]:
        prefer = str((ctx.params or {}).get("prefer") or "")
        return sorted(
            runs,
            key=lambda run: (
                0 if run.scenario_id == prefer else 1,
                -len(run.scenario_id),
                run.scenario_id,
                run.run_idx,
            ),
        )
