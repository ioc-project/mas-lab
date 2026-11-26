#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Keep the generator's run order."""

from __future__ import annotations

from mas.lab.benchmark.execution.roles import NoParams, PlannedRun, ScheduleContext


class AsGeneratedScheduler(NoParams):
    plugin_id = "as_generated"

    def order(self, runs: list[PlannedRun], ctx: ScheduleContext) -> list[PlannedRun]:
        return list(runs)
