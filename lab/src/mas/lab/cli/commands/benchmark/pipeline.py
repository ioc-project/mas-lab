#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab benchmark pipeline`` subcommands."""
from __future__ import annotations

import asyncio
from pathlib import Path

import click


@click.group("pipeline")
def pipeline_group() -> None:
    """Run a declarative post-processing pipeline (normalize, graph export, plot, …)."""


@pipeline_group.command("run")
@click.argument("config", type=Path, metavar="PIPELINE_YAML")
@click.option("--only", multiple=True, metavar="STEP",
              help="Run only these steps (dependencies included automatically).")
@click.option("--force", multiple=True, metavar="STEP",
              help="Force-rerun these steps (ignore cache).")
@click.option("--var", "template_vars", multiple=True, metavar="KEY=VALUE",
              help="Template variable(s) used in step config (e.g. --var events_jsonl=/path/events.jsonl).")
@click.option("--dry-run", is_flag=True, default=False,
              help="Propagate dry_run=True to every step: execute each step but skip real I/O.")
@click.option("--parallel/--no-parallel", default=True,
              help="Run independent steps concurrently (cap max_active). "
                   "Default on. Pass --no-parallel to serialize for debugging. "
                   "Incremental fork consumers fold records as they arrive.")
@click.option("-o", "--output-dir", type=Path, default=None,
              help="Override the pipeline's base output directory.")
def pipeline_run_cmd(config: Path, only: tuple, force: tuple, template_vars: tuple,
                     dry_run: bool, parallel: bool, output_dir: Path | None) -> None:
    """Execute a declarative pipeline YAML."""
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from mas.lab.benchmark.pipeline.cli import _load_lab_custom_steps
    from mas.lab.benchmark.pipeline import Pipeline, PipelineExecutor
    from mas.lab.benchmark.pipeline.config import PipelineEngineSpec
    from mas.lab.benchmark.pipeline.channel import install_channel

    _load_lab_custom_steps(config.resolve())
    try:
        install_channel(PipelineEngineSpec())
    except Exception:
        logging.getLogger(__name__).debug("stream channel not installed", exc_info=True)

    parsed_template_vars = {}
    for raw in template_vars:
        if "=" not in raw:
            raise SystemExit(f"Invalid --var format: {raw!r}. Expected KEY=VALUE.")
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise SystemExit(f"Invalid --var key in: {raw!r}")
        parsed_template_vars[key] = value

    pipeline = Pipeline.from_yaml(config)
    executor = PipelineExecutor(pipeline, output_dir=output_dir)
    result = asyncio.run(executor.run(
        steps=list(only) or None,
        force_rerun=list(force) or None,
        dry_run=dry_run,
        parallel=parallel,
        template_vars=parsed_template_vars,
    ))
    print(result.summary())
    raise SystemExit(0 if result.success else 1)


@pipeline_group.command("plan")
@click.argument("config", type=Path, metavar="PIPELINE_YAML")
@click.option("--only", multiple=True, metavar="STEP")
@click.option("--force", multiple=True, metavar="STEP")
@click.option("-v", "--verbose", is_flag=True, default=False)
def pipeline_plan_cmd(config: Path, only: tuple, force: tuple, verbose: bool) -> None:
    """Show the execution plan without running any steps."""
    from mas.lab.benchmark.pipeline.cli import _load_lab_custom_steps
    from mas.lab.benchmark.pipeline import Pipeline, PipelineExecutor

    _load_lab_custom_steps(config.resolve())

    pipeline = Pipeline.from_yaml(config)
    executor = PipelineExecutor(pipeline)
    plan = executor.plan(steps=list(only) or None, force_rerun=list(force) or None)
    print(plan.summary())
    if verbose:
        for i, name in enumerate(plan.execution_order, 1):
            step = pipeline.get_step(name)
            status = "RERUN" if name in plan.steps_to_rerun else "CACHED"
            deps = ", ".join(step.depends_on) if step.depends_on else "none"
            print(f"  {i}. [{status}] {name}  deps: {deps}")


@pipeline_group.command("show")
@click.argument("config", type=Path, metavar="PIPELINE_YAML")
@click.option("-v", "--verbose", is_flag=True, default=False)
def pipeline_show_cmd(config: Path, verbose: bool) -> None:
    """Show the pipeline structure (steps and dependencies)."""
    from mas.lab.benchmark.pipeline.cli import _load_lab_custom_steps
    from mas.lab.benchmark.pipeline import Pipeline

    _load_lab_custom_steps(config.resolve())

    pipeline = Pipeline.from_yaml(config)
    print(f"Pipeline: {pipeline.config.name} v{pipeline.config.version}")
    if pipeline.config.description:
        print(f"Description: {pipeline.config.description}")
    print()
    for i, step in enumerate(pipeline.steps, 1):
        deps = ", ".join(step.depends_on) if step.depends_on else "none"
        print(f"  {i}. {step.name}  type={step.type}  deps=[{deps}]")
        if verbose:
            print(f"     config: {step.config}")


@pipeline_group.command("validate")
@click.argument("config", type=Path, metavar="PIPELINE_YAML")
def pipeline_validate_cmd(config: Path) -> None:
    """Validate a pipeline YAML (checks steps, deps, and types)."""
    from mas.lab.benchmark.pipeline.cli import _load_lab_custom_steps
    from mas.lab.benchmark.pipeline import Pipeline

    _load_lab_custom_steps(config.resolve())

    try:
        pipeline = Pipeline.from_yaml(config)
        print(f"✓ Pipeline valid: {pipeline.config.name}  ({len(pipeline.steps)} steps)")
    except Exception as exc:
        print(f"✗ Validation failed: {exc}")
        raise SystemExit(1)
