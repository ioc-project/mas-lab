#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
from __future__ import annotations

"""clock_advance — move a virtual run clock, then push time to fixtures."""

from mas.lab.benchmark.environment.step import ActionResult, EnvironmentContext, EnvironmentStep
from mas.library.standard.lib.triggers import parse_duration


class ClockAdvanceStep(EnvironmentStep):
    type = "clock_advance"

    async def act(self, ctx: EnvironmentContext) -> ActionResult:
        raw = self.config.get("by")
        if raw is None:
            raise ValueError("clock_advance requires config.by")
        seconds = float(raw) if isinstance(raw, (int, float)) else parse_duration(str(raw))
        now = ctx.clock.advance(seconds)
        return ActionResult(payload={"virtual_s": now})
