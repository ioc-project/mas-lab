#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
from __future__ import annotations

from mas.lab.benchmark.environment.step import ActionResult, EnvironmentContext, EnvironmentStep


class SteerStep(EnvironmentStep):
    type = "steer"

    async def act(self, ctx: EnvironmentContext) -> ActionResult:
        control, session_id = ctx.target(self.config.get("session"), capability="steer")
        text = str(self.config.get("text") or self.config.get("content") or "")
        mode = str(self.config.get("mode") or "after")
        control.steer(session_id, text=text, mode=mode)
        return ActionResult(payload={})
