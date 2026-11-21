#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
from __future__ import annotations

"""sleep — wall pause, bounded by limits.max_sleep_s."""

import asyncio

from mas.lab.benchmark.environment.step import ActionResult, EnvironmentContext, EnvironmentStep
from mas.library.standard.lib.triggers import parse_duration


class SleepStep(EnvironmentStep):
    type = "sleep"

    async def act(self, ctx: EnvironmentContext) -> ActionResult:
        raw = self.config.get("for", self.config.get("by"))
        if raw is None:
            raise ValueError("sleep requires config.for")
        seconds = float(raw) if isinstance(raw, (int, float)) else parse_duration(str(raw))
        ctx.clock.check_sleep(seconds, bound_s=ctx.limits.max_sleep_s)
        await asyncio.sleep(seconds)  # cancellable: action_timeout_s can cut it short
        return ActionResult(payload={"duration_ms": round(seconds * 1000.0, 1)})
