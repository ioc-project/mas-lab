#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab benchmark run`` command."""
from __future__ import annotations

from pathlib import Path

import click


@click.command("run")
@click.argument("experiment_yaml", type=Path, metavar="EXPERIMENT_YAML")
@click.option("--force", is_flag=True, default=False,
              help="Force a new run even if a previous run exists (Makefile --force semantics).")
@click.option(
    "--override",
    "overrides",
    multiple=True,
    metavar="ROOT:PATH=VALUE",
    help="Schema-validated experiment override (repeatable; applied last).",
)
@click.option("--resume", is_flag=True, default=False, hidden=True,
              help="(Deprecated) Resume an interrupted run — default behaviour now.")
@click.option("--benchmark-id", default=None,
              help="Specific benchmark ID to resume.")
@click.option("--progress/--no-progress", default=True, show_default=True,
              help="Show real-time progress bar.")
@click.option("--dry-run", is_flag=True, default=False,
              help="Validate config and show what would be executed without running.")
@click.option("--max-runs", type=int, default=None,
              help="Override n_runs from YAML.")
@click.option("--limit-scenarios", type=int, default=None,
              help="Limit to first N scenarios.")
@click.option("--scenario-id", default=None,
              help="Run only the scenario with this ID.")
@click.option("--dataset-item", default=None,
              help="Run only the dataset item with this ID.")
@click.option("--sample-scenarios", type=int, default=None,
              help="Randomly sample N scenarios.")
@click.option("--single-run", is_flag=True, default=False,
              help="Run exactly 1 scenario for quick pipeline testing.")
@click.option("-o", "--output-dir", type=Path, default=None,
              help="Output directory for benchmark results.")
@click.option("--trace-cache", "trace_cache_dir", type=Path, default=None,
              help="Override trace-cache directory (CLI > YAML > MAS_TRACE_CACHE > $XDG_CACHE_HOME/mas/traces).")
@click.option("--data-cache", "data_cache_dir", type=Path, default=None,
              help="Override step-cache directory (CLI > MAS_DATA_CACHE > <output_dir>/.cache). "
                   "Use a shared path to reuse computed pipeline outputs after benchmark import.")
@click.option("--force-lock", is_flag=True, default=False,
              help="Force break existing lock.")
@click.option("--flavour", default=None,
              show_default=False,
              help=(
                  "Runtime flavour name resolved to <experiment_dir>/flavours/<name>.yaml. "
                  "Defaults to experiment's default_flavour, then 'local'."
              ))
@click.option("--infra", "infra", default=None,
              show_default=False,
              help=(
                  "Infra bundle name resolved to <experiment_dir>/infra/<name>.yaml. "
                  "Selects which infrastructure services (OTel collector, storage, …) "
                  "are provisioned by service_start / service_stop pipeline steps. "
                  "Defaults to experiment's default_infra (opt-in, no default)."
              ))
@click.option("--strategy",
              type=click.Choice(["coverage", "depth"], case_sensitive=False),
              default=None,
              help=(
                  "Execution ordering (overrides YAML schedule.ordering). "
                  "coverage (default): breadth-first — every condition gets one run before "
                  "any gets a second run, maximising coverage after partial execution. "
                  "depth: depth-first — complete all runs for each condition before moving on."
              ))
@click.option("--set", "step_overrides", multiple=True, metavar="STEP.KEY=VALUE",
              help=(
                  "Override a pipeline step config key from the CLI. "
                  "Format: STEP_TYPE.key=value (e.g. --set export_otel.destination=mock). "
                  "Value is auto-coerced: 'true'/'false' → bool, integers → int. "
                  "Can be repeated for multiple overrides."
              ))
@click.option(
    "--pipeline",
    "pipeline_attachments",
    multiple=True,
    metavar="LEVEL[:PHASE]:REF",
    help=(
        "Attach a pipeline YAML after experiment hooks (same order as repeated flags). "
        "Format: run:post:library:eti-apps/pipelines/observability/native-to-kg.yaml "
        "or run:library:... (phase defaults to post). "
        "Levels: run, test, scenario, application (experiment aliases application)."
    ),
)
@click.option(
    "-x", "--experiment-overlay",
    "experiment_overlays",
    multiple=True,
    metavar="OVERLAY_YAML",
    help=(
        "Apply an experiment overlay before running. "
        "Overlays deep-merge into experiment.yaml; pipeline hook lists (run.post, "
        "application.post, …) are appended rather than replaced, so overlays can "
        "add evaluation pipelines without removing base ones. "
        "Multiple -x flags are applied in order. "
        "Paths are resolved relative to experiment.yaml's directory."
    ),
)
@click.option("-b", "--background", "background", is_flag=True, default=False,
              help="Submit via controller daemon and return immediately (print worker id).")
@click.option("--clean-stale", "clean_stale", is_flag=True, default=False,
              help="Remove benchmark output for scenarios no longer in the experiment YAML "
                   "(and unreferenced trace-cache entries when enabled in config).")
def run_cmd(experiment_yaml: Path, force: bool, resume: bool, benchmark_id: str | None,
            overrides: tuple[str, ...],
            progress: bool, dry_run: bool, max_runs: int | None,
            limit_scenarios: int | None, sample_scenarios: int | None,
            scenario_id: str | None, dataset_item: str | None,
            single_run: bool, output_dir: Path | None, trace_cache_dir: Path | None,
            data_cache_dir: Path | None,
            force_lock: bool, flavour: str | None, infra: str | None, strategy: str | None,
            step_overrides: tuple[str, ...], pipeline_attachments: tuple[str, ...],
            experiment_overlays: tuple[str, ...],
            background: bool, clean_stale: bool) -> None:
    """Run a benchmark from an experiment YAML via the controller daemon.

    MAS ``--dry-run`` (without ``-b``) validates in-process — no daemon required.
    Legacy experiment dry-runs and all non-dry runs use the daemon. Without ``-b``,
    the CLI polls the worker and streams stdout/stderr until completion.

    Use ``-b``/``--background`` to submit and exit immediately with the worker id.
    """
    if dry_run and not background:
        from mas.lab.benchmark.engine import _is_mas_experiment_yaml
        from mas.lab.benchmark.worker import run_benchmark_sync

        if _is_mas_experiment_yaml(experiment_yaml.resolve()):
            ok = run_benchmark_sync(
                experiment_yaml,
                dry_run=True,
                overrides=list(overrides),
                force=force,
                max_runs=max_runs,
                limit_scenarios=limit_scenarios,
                scenario_id=scenario_id,
                dataset_item=dataset_item,
                sample_scenarios=sample_scenarios,
                single_run=single_run,
                output_dir=output_dir,
                trace_cache_dir=trace_cache_dir,
                data_cache_dir=data_cache_dir,
                force_lock=force_lock,
                flavour_name=flavour or "local",
                infra_name=infra,
                strategy=strategy,
                step_overrides=list(step_overrides),
                pipeline_attachments=list(pipeline_attachments),
                experiment_overlays=list(experiment_overlays),
                clean_stale=clean_stale or None,
            )
            raise SystemExit(0 if ok else 1)

    from mas.lab.controller.client import ControllerClient, follow_worker

    spec = {
        "experiment_yaml": str(experiment_yaml.resolve()),
        "overrides": list(overrides),
        "progress": progress,
        "resume": resume,
        "force": force,
        "benchmark_id": benchmark_id,
        "dry_run": dry_run,
        "max_runs": max_runs,
        "limit_scenarios": limit_scenarios,
        "scenario_id": scenario_id,
        "dataset_item": dataset_item,
        "sample_scenarios": sample_scenarios,
        "single_run": single_run,
        "output_dir": str(output_dir) if output_dir else None,
        "trace_cache_dir": str(trace_cache_dir) if trace_cache_dir else None,
        "data_cache_dir": str(data_cache_dir) if data_cache_dir else None,
        "force_lock": force_lock,
        "flavour_name": flavour,
        "infra_name": infra,
        "strategy": strategy,
        "step_overrides": list(step_overrides),
        "pipeline_attachments": list(pipeline_attachments),
        "experiment_overlays": list(experiment_overlays),
        "clean_stale": clean_stale,
    }

    client = ControllerClient()
    client.ensure_running(restart_stale=True)
    result = client.call("submit_benchmark", spec)
    worker_id = result["worker_id"]

    if background:
        click.echo(worker_id)
        raise SystemExit(0)

    detail = follow_worker(worker_id, poll=0.5, stream=True)
    ok = detail.get("status") == "completed" and (detail.get("exit_code") in (0, None))
    raise SystemExit(0 if ok else 1)
