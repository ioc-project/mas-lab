#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Wait until a scheduled batch is terminal, then expose its status."""

from __future__ import annotations

import asyncio
from typing import Any

from mas.lab.benchmark.control import TERMINAL, batch_status, get_batch
from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.executor import ExecutionContext
from mas.lab.benchmark.pipeline.models import ConfigParam
from mas.library.lab.steps.control._util import setting


class AwaitBatchStep(PipelineStep):
    """Read a batch's status until it finishes (or the timeout)."""

    type = "await_batch"
    worker_safe = True
    mode = "batch"

    PARAMS = [
        ConfigParam(
            "batch_id",
            str,
            default=None,
            description="Batch id. Defaults to the upstream schedule_experiment.",
        ),
        ConfigParam("timeout_s", float, default=3600.0, description="Seconds to wait before raising."),
        ConfigParam("poll_s", float, default=0.05, description="Poll interval in seconds."),
    ]

    async def execute(self, ctx: ExecutionContext) -> StepOutput:
        batch_id = setting(ctx, self, "batch_id")
        if not batch_id:
            raise ValueError(f"step {self.name!r}: await_batch needs a batch_id or an upstream schedule_experiment")
        timeout_s = float(self.config.get("timeout_s") or 3600.0)
        poll_s = max(0.0, float(self.config.get("poll_s") or 0.05))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout_s)
        snapshot: dict[str, Any] = {}
        while True:
            snapshot = await asyncio.to_thread(batch_status, batch_id)
            if str(snapshot.get("status") or "") in TERMINAL:
                break
            if loop.time() >= deadline:
                raise TimeoutError(f"step {self.name!r}: timed out waiting for batch {batch_id}")
            if poll_s:
                await asyncio.sleep(poll_s)
            else:
                await asyncio.sleep(0)
        handle = get_batch(batch_id)
        return StepOutput(
            data={"batch": snapshot, "batch_id": batch_id, "runs": list(handle.runs) if handle else []},
            metadata={"batch_id": batch_id, "status": snapshot.get("status")},
        )
