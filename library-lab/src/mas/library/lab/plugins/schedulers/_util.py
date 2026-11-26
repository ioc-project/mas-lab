#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared helpers for builtin schedulers."""

from __future__ import annotations

from mas.lab.benchmark.execution.roles import PlannedRun, ScheduleContext


def scenario_item_index(runs: list[PlannedRun], ctx: ScheduleContext) -> tuple[dict[str, int], dict[int, int]]:
    """Stable indexes: declared scenario order, then first appearance of each item."""
    scenario_index = {sid: i for i, sid in enumerate(ctx.scenario_ids)}
    item_index: dict[int, int] = {}
    for run in runs:
        key = id(run.item)
        if key not in item_index:
            item_index[key] = len(item_index)
    return scenario_index, item_index
