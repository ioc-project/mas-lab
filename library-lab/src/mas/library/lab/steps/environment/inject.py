#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
from __future__ import annotations

from mas.lab.benchmark.environment.step import ActionResult, EnvironmentContext, EnvironmentStep


class InjectStep(EnvironmentStep):
    type = "inject"

    async def act(self, ctx: EnvironmentContext) -> ActionResult:
        control, session_id = ctx.target(self.config.get("session"), capability="enqueue_input")
        text = str(self.config.get("text") or self.config.get("content") or "")
        control.enqueue_input(session_id, text=text, source="environment")
        return ActionResult(payload={})
