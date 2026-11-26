#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab benchmark runs``: plan, shard, merge, retry and summarize, one step per process.

Each subcommand is one role (generation and scheduling, retry, analysis) working on files
only, so a CI system can run them as separate jobs and run the runs on many machines:

    runs plan EXP        generate + schedule -> plan.json, shard-N.json, matrix.json
    run EXP --run-plan shard-N.json -o out-N       (one CI job per shard)
    runs merge out-* -o merged
    runs retry EXP --plan plan.json --results merged   -> next round's plan and matrix
    runs summary EXP --plan plan.json --results merged -> completeness report
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click
from mas.lab.benchmark.execution.planner import write_plan_for
from mas.lab.benchmark.execution.roles import AnalysisContext, validated_plugin
from mas.lab.benchmark.execution.wire import (
    DEFAULT_MAX_SHARDS,
    SHARD_MODES,
    make_shards,
    merge_results,
    read_results,
    runs_to_retry,
)

_FILE = click.Path(exists=True, dir_okay=False, path_type=Path)
_DIR = click.Path(exists=True, file_okay=False, path_type=Path)


def _experiment_flags(fn):
    """The flags that must match between plan, run, retry and summary."""
    for option in (
        click.option("--max-runs", type=int, default=None, help="Override n_runs from YAML."),
        click.option("--override", "overrides", multiple=True, metavar="PATH=VALUE", help="Experiment override."),
        click.option(
            "-x",
            "--experiment-overlay",
            "overlays",
            multiple=True,
            help="Experiment overlay, as for `benchmark run` (repeatable). Plan and run must use the same ones.",
        ),
    ):
        fn = option(fn)
    return fn


def _shard_flags(fn):
    for option in (
        click.option("--scheduler", default=None, help="Registered scheduler id; overrides schedule.plugin."),
        click.option(
            "--shards-dir",
            type=click.Path(file_okay=False, path_type=Path),
            default=None,
            help="Write shard-N.json and matrix.json here (default: next to the plan file).",
        ),
        click.option(
            "--shard-by",
            type=click.Choice(SHARD_MODES),
            default="run",
            show_default=True,
            help="What one CI job runs.",
        ),
        click.option(
            "--shard-size", type=int, default=1, show_default=True, help="Runs per shard for --shard-by chunk."
        ),
        click.option(
            "--max-shards",
            type=int,
            default=DEFAULT_MAX_SHARDS,
            show_default=True,
            help="Most shards to emit (a GitHub Actions matrix holds 256 jobs).",
        ),
    ):
        fn = option(fn)
    return fn


def _load(experiment_yaml, overrides, max_runs, overlays, *, scheduler=None, plan_file=None):
    """Plan the experiment; with ``plan_file``, only the runs it names."""
    from mas.lab.benchmark.execution.planner import plan_experiment
    from mas.lab.benchmark.execution.wire import read_plan, select_runs

    loaded, runs = plan_experiment(
        experiment_yaml,
        overrides=list(overrides),
        scheduler=scheduler,
        max_runs=max_runs,
        experiment_overlays=list(overlays),
    )
    return loaded, (select_runs(runs, read_plan(plan_file))[0] if plan_file else runs)


def _dir_names(loaded: Any) -> dict[str, str]:
    return {s.id: s.output_dir_name for s in loaded.exp.scenarios}


def _emit(loaded, runs, attempts, plan_path: Path, opts: dict, **extra) -> dict:
    """Write the plan, one file per shard, and the matrix. Returns the matrix."""
    directory = opts["shards_dir"] or plan_path.parent
    directory.mkdir(parents=True, exist_ok=True)
    sched = opts["scheduler"]
    write_plan_for(plan_path, loaded, runs, scheduler=sched, attempts_before=attempts, **extra)
    shards = make_shards(runs, by=opts["shard_by"], size=opts["shard_size"], max_shards=opts["max_shards"])
    include = []
    for index, group in enumerate(shards):
        name = f"shard-{index}.json"
        write_plan_for(directory / name, loaded, group, scheduler=sched, attempts_before=attempts)
        include.append({"shard": index, "file": name, "n_runs": len(group)})
    matrix = {"include": include}
    (directory / "matrix.json").write_text(json.dumps(matrix) + "\n", encoding="utf-8")
    return matrix


def _shard_opts(**kwargs) -> dict:
    return {k: kwargs[k] for k in ("scheduler", "shards_dir", "shard_by", "shard_size", "max_shards")}


@click.group("runs")
def runs_group() -> None:
    """Plan, shard, merge, retry and summarize runs as separate steps (see the module help)."""


@runs_group.command("plan")
@click.argument("experiment_yaml", type=_FILE)
@click.option(
    "-o",
    "--output",
    "plan_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("plan.json"),
    show_default=True,
    help="Where to write the full plan.",
)
@_experiment_flags
@_shard_flags
def plan_cmd(experiment_yaml, plan_path, overlays, overrides, max_runs, **shard):
    """Generate and schedule the runs; write the plan and its shards. Runs nothing."""
    loaded, runs = _load(experiment_yaml, overrides, max_runs, overlays, scheduler=shard["scheduler"])
    matrix = _emit(loaded, runs, None, plan_path, _shard_opts(**shard))
    click.echo(f"{len(runs)} runs, {len(matrix['include'])} shards -> {plan_path}")


@runs_group.command("retry")
@click.argument("experiment_yaml", type=_FILE)
@click.option("--plan", "plan_file", type=_FILE, required=True)
@click.option("--results", "results_dir", type=_DIR, required=True, help="Merged results tree of the round that ran.")
@click.option(
    "-o",
    "--output",
    "retry_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("retry-plan.json"),
    show_default=True,
)
@_experiment_flags
@_shard_flags
def retry_cmd(experiment_yaml, plan_file, results_dir, retry_path, overlays, overrides, max_runs, **shard):
    """Ask the experiment's retrier which incomplete runs another round should run."""
    loaded, runs = _load(experiment_yaml, overrides, max_runs, overlays, plan_file=plan_file)
    retrier = validated_plugin("retrier", loaded.exp.retry.plugin, dict(loaded.exp.retry.params))
    results = read_results(results_dir, runs, _dir_names(loaded))
    again, spent, gave_up = runs_to_retry(runs, results, retrier)
    matrix = _emit(loaded, again, spent, retry_path, _shard_opts(**shard), gave_up=gave_up)
    click.echo(f"{len(again)} runs to retry, {len(gave_up)} given up, {len(matrix['include'])} shards -> {retry_path}")


@runs_group.command("merge")
@click.argument("sources", nargs=-1, required=True, type=_DIR)
@click.option("-o", "--output", "dest", type=click.Path(file_okay=False, path_type=Path), required=True)
def merge_cmd(sources, dest):
    """Merge shard results trees; an ``ok`` folder is never replaced by a failed one."""
    click.echo(json.dumps(merge_results(list(sources), dest)))


@runs_group.command("summary")
@click.argument("experiment_yaml", type=_FILE)
@click.option("--plan", "plan_file", type=_FILE, required=True)
@click.option("--results", "results_dir", type=_DIR, required=True)
@click.option("--fail-on-incomplete", is_flag=True, default=False, help="Exit 1 when any planned run is incomplete.")
@_experiment_flags
def summary_cmd(experiment_yaml, plan_file, results_dir, fail_on_incomplete, overlays, overrides, max_runs):
    """Run the experiment's analyzer over a results tree and print its report."""
    loaded, runs = _load(experiment_yaml, overrides, max_runs, overlays, plan_file=plan_file)
    params = dict(loaded.exp.analyzer.params)
    analyzer = validated_plugin("analyzer", loaded.exp.analyzer.plugin, params)
    context = AnalysisContext(results_root=results_dir, runs=runs, dir_names=_dir_names(loaded), params=params)
    report = analyzer.analyze(context)
    click.echo(json.dumps(report, indent=2))
    if fail_on_incomplete and report.get("incomplete"):
        raise SystemExit(1)
