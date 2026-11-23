#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
from __future__ import annotations

import logging

from mas.lab.benchmark.environment.step import ActionResult, EnvironmentContext, EnvironmentStep

logger = logging.getLogger(__name__)


class KillSwitchStep(EnvironmentStep):
    type = "kill_switch"

    async def act(self, ctx: EnvironmentContext) -> ActionResult:
        control, session_id = ctx.target(self.config.get("session"), capability="cancel_inflight")
        reason = str(self.config.get("reason") or "environment")
        cancelled = bool(control.cancel_inflight(session_id))
        persisted = True
        try:
            control.persist(session_id, label=reason, auto_stop=True)
        except Exception:
            persisted = False
            logger.warning("kill_switch: persist failed for session %s; pausing instead", session_id, exc_info=True)
            control.pause(session_id, reason=reason)
        if not cancelled:
            logger.warning(
                "kill_switch: nothing was in flight for session %s; the turn continues (fire it from a "
                "wall timer while the model is deciding)",
                session_id,
            )
        # Say what actually happened: nothing is in flight at a tool result, and persist needs a
        # checkpoint store. The log must not read as "the turn was stopped" when it was only paused.
        return ActionResult(payload={"cancelled": int(cancelled), "persisted": int(persisted)})
