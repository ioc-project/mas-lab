#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Ask the lab API for the next runs (halving, leftover errors, score-driven)."""

from __future__ import annotations

from mas.lab.benchmark.control import get_batch, next_runs
from mas.lab.benchmark.execution.planner import plan_experiment, write_plan_for
from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.executor import ExecutionContext
from mas.lab.benchmark.pipeline.models import ConfigParam
from mas.library.lab.steps.control._util import load_scores, resolve_rel, setting, upstream_runs


class NextRunsStep(PipelineStep):
    """Compute the next runs from scores and/or a batch's incomplete runs."""

    type = "next_runs"
    worker_safe = True
    mode = "batch"

    PARAMS = [
        ConfigParam(
            "strategy",
            str,
            default="remaining",
            description="remaining | successive_halving | score_driven.",
        ),
        ConfigParam(
            "keep_fraction",
            float,
            default=0.5,
            description="Fraction of scenarios to keep (halving).",
        ),
        ConfigParam(
            "keep_n",
            int,
            default=None,
            description="Exact number of scenarios to keep (overrides fraction).",
        ),
        ConfigParam("scores_path", str, default=None, description="JSON mapping scenario id to score."),
        ConfigParam(
            "batch_id",
            str,
            default=None,
            description="Batch whose incomplete runs are the pool.",
        ),
        ConfigParam(
            "experiment",
            str,
            default=None,
            description="Experiment YAML to plan when there is no batch and no upstream runs.",
        ),
        ConfigParam(
            "plan_path",
            str,
            default=None,
            description="Write the chosen runs as a run-plan file (needs `experiment`) for `benchmark run --run-plan`.",
        ),
        ConfigParam(
            "score_key",
            str,
            default="score",
            description="Field name when scores come from an upstream table.",
        ),
    ]

    async def execute(self, ctx: ExecutionContext) -> StepOutput:
        strategy = str(self.config.get("strategy") or "remaining")
        scores = load_scores(self, ctx)
        batch_id = setting(ctx, self, "batch_id")
        handle = get_batch(batch_id) if batch_id else None
        pool = None if handle else upstream_runs(ctx, self)
        loaded = None
        if self.config.get("experiment"):
            loaded, planned = plan_experiment(resolve_rel(ctx, str(self.config["experiment"])))
            pool = pool or planned
        if handle is None and not pool:
            raise ValueError(f"step {self.name!r}: next_runs needs a batch, upstream runs, or config.experiment")
        keep_fraction = self.config.get("keep_fraction")
        runs = next_runs(
            handle=handle,
            runs=pool,
            scores=scores.scenarios,
            strategy=strategy,
            keep_fraction=0.5 if keep_fraction is None else float(keep_fraction),
            keep_n=self.config.get("keep_n"),
        )
        plan_file = ""
        if self.config.get("plan_path"):
            if loaded is None:
                raise ValueError(f"step {self.name!r}: plan_path needs config.experiment")
            plan_file = str(write_plan_for(resolve_rel(ctx, str(self.config["plan_path"])), loaded, runs))
        return StepOutput(
            data={
                "runs": runs,
                "run_rows": [run.row() for run in runs],
                "n_runs": len(runs),
                "strategy": strategy,
                "plan_file": plan_file,
                "scores": scores.scenarios,
            },
            metadata={"n_runs": len(runs), "strategy": strategy},
        )
