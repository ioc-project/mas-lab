#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab benchmark clean`` command."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import click


@click.command("clean")
@click.argument("experiment_yaml", type=Path, metavar="EXPERIMENT_YAML")
@click.option("--scenario", "scenarios", multiple=True, metavar="SCENARIO_ID",
              help="Scenario ID(s) to clean. Repeatable. If omitted, ALL scenarios are cleaned.")
@click.option("--dry-run", is_flag=True, default=False,
              help="Print what would be deleted without deleting anything.")
@click.option("--keep-traces", is_flag=True, default=False,
              help="Delete only the scenario run dirs; keep the global trace-cache entries.")
@click.option("-o", "--output-dir", type=Path, default=None,
              help="Override the output dir directly (use when experiment.yaml is unavailable; pass '-' as EXPERIMENT_YAML).")  # noqa: E501
def clean_cmd(
    experiment_yaml: Path,
    scenarios: tuple[str, ...],
    dry_run: bool,
    keep_traces: bool,
    output_dir: Path | None,
) -> None:
    """Delete cached runs for selected scenarios so they are re-executed on the next run.

    Examples:

    \b
    # Clean only the ToT scenario
    mas-lab benchmark clean labs/design-space.lab/01-design-patterns/experiment.yaml \\
        --scenario pattern-tree-of-thoughts

    \b
    # Dry-run: see what would be removed
    mas-lab benchmark clean labs/design-space.lab/01-design-patterns/experiment.yaml \\
        --scenario pattern-tree-of-thoughts --dry-run

    \b
    # Clean all scenarios in an experiment
    mas-lab benchmark clean labs/design-space.lab/01-design-patterns/experiment.yaml
    """
    from mas.lab.benchmark.cli import clean_command

    args = SimpleNamespace(
        experiment_yaml=experiment_yaml,
        scenarios=list(scenarios) if scenarios else None,
        dry_run=dry_run,
        keep_traces=keep_traces,
        output_dir=output_dir,
    )
    raise SystemExit(clean_command(args))
