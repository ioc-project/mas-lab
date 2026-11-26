#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Schedule a new experiment batch from a pipeline step."""

from __future__ import annotations

import asyncio
from pathlib import Path

from mas.lab.benchmark.control import DEFAULT_MAX_SCHEDULED_BATCHES, pipeline_budget, schedule_batch
from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.executor import ExecutionContext
from mas.lab.benchmark.pipeline.models import ConfigParam
from mas.library.lab.steps.control._util import resolve_rel, setting


class ScheduleExperimentStep(PipelineStep):
    """Call the lab API to start another experiment batch."""

    type = "schedule_experiment"
    worker_safe = True
    mode = "batch"
    ins = ("experiment_yaml",)
    out = ("batch",)

    PARAMS = [
        ConfigParam(
            "experiment",
            str,
            default=None,
            description="Path to the experiment YAML. Defaults to an upstream inject_experiment.",
        ),
        ConfigParam(
            "max_scheduled_batches",
            int,
            default=DEFAULT_MAX_SCHEDULED_BATCHES,
            description="Bound on how many batches this pipeline may start.",
        ),
        ConfigParam("output_dir", str, default=None, description="Optional output directory for the inner batch."),
        ConfigParam(
            "plan_file",
            str,
            default=None,
            description="PlannedRun-plan file naming the runs to run. Defaults to an upstream step's `plan_file`.",
        ),
    ]

    async def execute(self, ctx: ExecutionContext) -> StepOutput:
        raw = setting(ctx, self, "experiment", "experiment_yaml", "experiment")
        if not raw:
            raise ValueError(
                f"step {self.name!r}: schedule_experiment needs config.experiment or an upstream experiment_yaml"
            )
        path = resolve_rel(ctx, str(raw))
        max_batches = self.config.get("max_scheduled_batches", DEFAULT_MAX_SCHEDULED_BATCHES)
        output_raw = self.config.get("output_dir")
        output_dir = Path(str(output_raw)) if output_raw else None
        runs_raw = setting(ctx, self, "plan_file")
        handle = await asyncio.to_thread(
            schedule_batch,
            path,
            budget=pipeline_budget(ctx.pipeline),
            output_dir=output_dir,
            lineage_dir=ctx.output_dir,
            plan_file=resolve_rel(ctx, str(runs_raw)) if runs_raw else None,
            max_scheduled_batches=int(max_batches),
        )
        return StepOutput(
            data={"batch": handle.snapshot(), "batch_id": handle.batch_id, "experiment_yaml": str(path)},
            metadata={"batch_id": handle.batch_id},
        )
