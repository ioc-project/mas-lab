#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
from __future__ import annotations

from mas.lab.benchmark.environment.step import ActionResult, EnvironmentContext, EnvironmentStep


class ResumeStep(EnvironmentStep):
    type = "resume"

    async def act(self, ctx: EnvironmentContext) -> ActionResult:
        control, session_id = ctx.target(self.config.get("session"), capability="resume")
        control.resume(session_id)
        return ActionResult(payload={})
