#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Write a follow-up experiment YAML whose dataset is the proposed user prompts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.executor import ExecutionContext
from mas.lab.benchmark.pipeline.models import ConfigParam
from mas.library.lab.steps.control._util import (
    resolve_rel,
    safe_item_id,
    setting,
    under,
    upstream_prompts,
    upstream_runs,
)
from mas.runtime.secrets import load_checked_document


class InjectExperimentStep(PipelineStep):
    """Clone a template experiment and inject the next user prompts as dataset items."""

    type = "inject_experiment"
    worker_safe = True
    mode = "batch"
    ins = ("prompts", "runs")
    out = ("experiment_yaml",)

    PARAMS = [
        ConfigParam(
            "experiment",
            str,
            default=None,
            description="Template experiment YAML. Defaults to an upstream path.",
        ),
        ConfigParam(
            "output",
            str,
            default="next/experiment.yaml",
            description="Where to write the new experiment, relative to the step output dir.",
        ),
        ConfigParam(
            "dataset_output",
            str,
            default="next/dataset.yaml",
            description="Where to write the injected dataset, relative to the step output dir.",
        ),
        ConfigParam(
            "pin_runs",
            bool,
            default=True,
            description="Write a run-plan file (`plan_file`) restricted to the scenarios of upstream runs.",
        ),
    ]

    async def execute(self, ctx: ExecutionContext) -> StepOutput:
        prompts = upstream_prompts(ctx, self)
        if not prompts:
            raise ValueError(f"step {self.name!r}: inject_experiment needs upstream prompts")
        raw = setting(ctx, self, "experiment", "experiment_yaml", "experiment")
        if not raw:
            raise ValueError(f"step {self.name!r}: inject_experiment needs config.experiment")
        template = resolve_rel(ctx, str(raw))
        if not template.is_file():
            raise FileNotFoundError(f"step {self.name!r}: experiment yaml not found: {template}")
        exp_path = under(ctx.output_dir, str(self.config.get("output") or "next/experiment.yaml"))
        ds_path = under(ctx.output_dir, str(self.config.get("dataset_output") or "next/dataset.yaml"))
        taken: set[str] = set()
        items = [
            {"id": safe_item_id(row.get("id"), f"p{i}", taken), "inputs": {"user": str(row["user_prompt"])}}
            for i, row in enumerate(prompts, 1)
        ]
        dataset = {
            "apiVersion": "lab/v1",
            "kind": "Dataset",
            "metadata": {"name": "injected-prompts"},
            "spec": {"items": items},
        }
        loaded = load_checked_document(template, name=f"step {self.name!r} template")
        wrapped = "experiment" in loaded
        body = _absolutize(dict(loaded.get("experiment") or loaded), template.parent)
        try:
            ds_rel = ds_path.resolve().relative_to(exp_path.parent.resolve())
            body["dataset"] = {"path": f"./{ds_rel.as_posix()}"}
        except ValueError:
            body["dataset"] = {"path": str(ds_path.resolve())}
        out_doc: dict[str, Any] = {"experiment": body} if wrapped else body
        exp_path.parent.mkdir(parents=True, exist_ok=True)
        ds_path.parent.mkdir(parents=True, exist_ok=True)
        ds_path.write_text(yaml.safe_dump(dataset, sort_keys=False, allow_unicode=True), encoding="utf-8")
        exp_path.write_text(yaml.safe_dump(out_doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
        files = [exp_path, ds_path]
        plan_file = ""
        upstream = upstream_runs(ctx, self)
        if bool(self.config.get("pin_runs", True)) and upstream:
            plan_file = str(
                _write_pinned_plan(exp_path, {c.scenario_id for c in upstream}, exp_path.parent / "runs.json")
            )
            files.append(Path(plan_file))
        return StepOutput(
            data={
                "experiment_yaml": str(exp_path),
                "plan_file": plan_file,
                "dataset_yaml": str(ds_path),
                "n_prompts": len(items),
                "prompts": prompts,
            },
            files=files,
            metadata={"experiment_yaml": str(exp_path), "n_prompts": len(items)},
        )


def _absolutize(node: Any, template_dir: Path) -> Any:
    """Make every ``./`` or ``../`` path in the template absolute.

    The new experiment is written elsewhere, so a relative path (manifest, overlay,
    pipeline, fixture) would otherwise point at the wrong directory.
    """
    if isinstance(node, dict):
        return {key: _absolutize(value, template_dir) for key, value in node.items()}
    if isinstance(node, list):
        return [_absolutize(value, template_dir) for value in node]
    if isinstance(node, str) and (node.startswith("./") or node.startswith("../")):
        return str((template_dir / node).resolve())
    return node


def _write_pinned_plan(experiment_yaml: Path, keep: set[str], target: Path) -> Path:
    """Plan the new experiment and keep only the runs of ``keep`` scenarios.

    The design stays whole (a factorial still names every combination); the plan file,
    not an edited design, narrows the next batch.
    """
    from mas.lab.benchmark.execution.planner import plan_experiment, write_plan_for

    loaded, runs = plan_experiment(experiment_yaml)
    kept = [run for run in runs if run.scenario_id in keep]
    if not kept:
        raise ValueError(f"none of the scenarios {sorted(keep)} has a run in {experiment_yaml}")
    return write_plan_for(target, loaded, kept)
