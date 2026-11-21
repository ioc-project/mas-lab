#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
from __future__ import annotations

"""emit — record a user.* environment event (value-free payload)."""

from mas.lab.benchmark.environment.step import ActionResult, EnvironmentContext, EnvironmentStep


class EmitStep(EnvironmentStep):
    type = "emit"

    async def act(self, ctx: EnvironmentContext) -> ActionResult:
        name = str(self.config.get("name") or "").strip()
        if not name:
            raise ValueError("emit requires config.name")
        folded = name if name.startswith("user.") else f"user.{name}"
        payload = ctx.interpolate(self.config.get("payload") or {})
        if ctx.engine is not None:
            ctx.engine.emit_named(folded, payload)
        else:
            ctx.events.emit(folded, source={"type": "environment", "id": "emit"}, payload=payload)
        return ActionResult(payload={})
