#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Run-major walk: one pass across every run before the next replicate."""

from __future__ import annotations

from mas.lab.benchmark.execution.roles import NoParams, PlannedRun, ScheduleContext

from ._util import scenario_item_index


class CoverageScheduler(NoParams):
    plugin_id = "coverage"

    def order(self, runs: list[PlannedRun], ctx: ScheduleContext) -> list[PlannedRun]:
        scenario_index, item_index = scenario_item_index(runs, ctx)
        return sorted(
            runs,
            key=lambda run: (
                run.run_idx,
                scenario_index.get(run.scenario_id, 10**9),
                item_index.get(id(run.item), 10**9),
            ),
        )
