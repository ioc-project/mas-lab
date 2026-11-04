#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab benchmark follow`` command."""
from __future__ import annotations

import json as _json
import time
from pathlib import Path

import click


@click.command("follow")
@click.argument("experiment_yaml", type=Path, metavar="EXPERIMENT_YAML", required=False)
@click.option("--interval", type=float, default=5.0, show_default=True,
              help="Polling interval in seconds.")
@click.option("--once", is_flag=True, default=False,
              help="Print a single progress snapshot and exit immediately.")
@click.option("-o", "--output-dir", type=Path, default=None,
              help="Override the experiment output directory.")
def follow_cmd(experiment_yaml: Path | None, interval: float, once: bool,
               output_dir: Path | None) -> None:
    """Watch live progress of a running benchmark.

    Polls run_info.json files under the experiment output directory and
    prints a running tally of completed / failed / total runs.  Exits
    automatically when all expected runs have finished.

    Press Ctrl-C to stop watching at any time.

    Examples:

    \b
      mas-lab benchmark follow experiments/01-semantic-grouping/experiment.yaml
      mas-lab benchmark follow --once experiments/01-semantic-grouping/experiment.yaml
    """
    _out: Path | None = None
    total: int | None = None

    if experiment_yaml is not None:
        experiment_yaml = experiment_yaml.expanduser().resolve()
        if not experiment_yaml.exists():
            raise click.ClickException(f"Experiment YAML not found: {experiment_yaml}")

        from mas.lab.lab.config import MASExperimentConfig
        exp = MASExperimentConfig.from_yaml(experiment_yaml)

        dataset_items: list = []
        if exp.dataset and exp.dataset.exists():
            try:
                with open(exp.dataset) as _f:
                    _ds = _json.load(_f)
                dataset_items = _ds.get("items", [])
            except Exception:
                pass
        if not dataset_items:
            dataset_items = [{}]

        n_runs: int = getattr(exp, "n_runs", None) or (
            (exp.execution.n_runs if exp.execution else 1) or 1
        )
        n_scenarios: int = len(exp.scenario_ids())
        total = n_scenarios * len(dataset_items) * n_runs

        _out = output_dir or exp.output_dir
    else:
        _out = output_dir

    if _out is None:
        raise click.ClickException(
            "Cannot determine output directory — pass EXPERIMENT_YAML or use -o/--output-dir."
        )

    _out = Path(_out).expanduser().resolve()
    if not _out.exists():
        raise click.ClickException(f"Output directory not found: {_out}")

    def _snapshot():
        counts = {}
        for p in _out.rglob("run_info.json"):  # type: ignore[union-attr]
            try:
                info = _json.loads(p.read_text())
                s = str(info.get("status", "unknown"))
            except Exception:
                s = "unknown"
            counts[s] = counts.get(s, 0) + 1
        return counts

    def _render(counts, elapsed_s):
        done = counts.get("ok", 0)
        err = counts.get("error", 0)
        finished = done + err
        total_str = f"/{total}" if total is not None else ""
        pct_str = f"  {100 * finished / total:5.1f}%" if total else ""
        err_str = f"  err={err}" if err else ""
        mins, secs = divmod(int(elapsed_s), 60)
        elapsed_str = f"{mins}m{secs:02d}s" if mins else f"{secs}s"
        return f"ok={done}{err_str}  done={finished}{total_str}{pct_str}  elapsed={elapsed_str}"

    start = time.monotonic()
    try:
        while True:
            counts = _snapshot()
            finished = counts.get("ok", 0) + counts.get("error", 0)
            line = _render(counts, time.monotonic() - start)

            if once:
                click.echo(line)
                break

            click.echo("\033[2K\r" + line, nl=False)

            if total is not None and finished >= total:
                click.echo("")
                click.echo(f"Benchmark complete — {finished} runs finished.")
                break

            time.sleep(interval)

    except KeyboardInterrupt:
        click.echo("")
