#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab benchmark list`` command."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import click


@click.command("list")
@click.option("--status", default=None,
              help="Filter by status (running, completed, failed, …).")
@click.option("--experiment", type=Path, default=None,
              help="Filter to runs matching this experiment.yaml path.")
@click.option("--limit", type=int, default=20, show_default=True,
              help="Maximum number of runs to show.")
@click.option("--format", "fmt",
              type=click.Choice(["simple","plain","grid","pipe","markdown","csv","tsv","json","jsonl"]),
              default="simple", show_default=True, help="Output format.")
@click.option("-o", "--output-dir", type=Path, default=None,
              help="Benchmarks root directory.")
def list_cmd(status: str | None, experiment: Path | None, limit: int, fmt: str, output_dir: Path | None) -> None:
    """List all benchmark runs."""
    from mas.lab.benchmark.cli import list_command

    args = SimpleNamespace(status=status, experiment=experiment, limit=limit, format=fmt, output_dir=output_dir)
    raise SystemExit(list_command(args))
