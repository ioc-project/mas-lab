#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab benchmark rename`` command."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import click


@click.command("rename")
@click.argument("benchmark_id")
@click.argument("new_slug")
@click.option("--dry-run", is_flag=True, default=False,
              help="Show what would be done without touching the filesystem.")
@click.option("-o", "--output-dir", type=Path, default=None,
              help="Override the benchmarks root directory.")
def rename_cmd(
    benchmark_id: str,
    new_slug: str,
    dry_run: bool,
    output_dir: Path | None,
) -> None:
    """Rename a benchmark run directory to NEW_SLUG.

    Performs an atomic directory rename (same filesystem — no data copy) and
    patches the absolute path fields inside ``metadata.yaml`` so the benchmark
    remains accessible under the new name.

    \b
    Examples
    --------
    # Rename by short ID
    mas-lab benchmark rename a1b2c3d4 trip-planner-d3-gemini

    # Preview without touching files
    mas-lab benchmark rename a1b2c3d4 trip-planner-d3-gemini --dry-run
    """
    from mas.lab.benchmark.cli import rename_command

    args = SimpleNamespace(benchmark_id=benchmark_id, new_slug=new_slug,
                           dry_run=dry_run, output_dir=output_dir)
    raise SystemExit(rename_command(args))
