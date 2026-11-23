#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
from __future__ import annotations

from mas.lab.benchmark.environment.step import ActionResult, EnvironmentContext, EnvironmentStep


class CheckpointStep(EnvironmentStep):
    type = "checkpoint"

    async def act(self, ctx: EnvironmentContext) -> ActionResult:
        control, session_id = ctx.target(self.config.get("session"), capability="snapshot")
        label = str(self.config.get("label") or "environment")
        persist = bool(self.config.get("persist"))
        # A snapshot needs a stopped session, and a trigger fires while the kernel waits at the
        # governance point: stop by default. Pair with a `resume` step to carry on.
        auto_stop = bool(self.config.get("auto_stop", True))
        control.snapshot(session_id, label=label, auto_stop=auto_stop)
        if persist:
            control.persist(session_id, label=label, auto_stop=auto_stop)
        return ActionResult(payload={})
