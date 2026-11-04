#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab benchmark migrate`` command."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import click


@click.command("migrate")
@click.argument("source_dir", type=Path, metavar="SOURCE_DIR")
@click.argument("target_dir", type=Path, metavar="TARGET_DIR", required=False)
@click.option("--no-symlink", is_flag=True, default=False,
              help="Do not replace SOURCE_DIR with a symlink after copying.")
@click.option("--dry-run", is_flag=True, default=False,
              help="Show what would be done without touching the filesystem.")
@click.option("--mas-data-root", type=Path, default=None,
              help="Override the labs_root() root used for auto-derived targets "
                   "(default: labs_root()).",)
def migrate_cmd(
    source_dir: Path,
    target_dir: Path | None,
    no_symlink: bool,
    dry_run: bool,
    mas_data_root: Path | None,
) -> None:
    """Move a benchmark run directory to labs_root()/ (or TARGET_DIR).

    SOURCE_DIR must contain a ``metadata.yaml`` file (benchmark run folder).

    TARGET_DIR is optional — when omitted mas-lab derives a canonical path
    under ``labs_root()/<experiment_name>/``.

    After copying the data mas-lab patches all absolute paths inside the
    copied ``metadata.yaml`` so the benchmark remains accessible via the
    new location.

    Unless ``--no-symlink`` is set, SOURCE_DIR is replaced by a symlink that
    transparently points to TARGET_DIR, keeping any existing relative-path
    references working.

    \b
    Examples
    --------
    # Auto-derive target from the experiment name
    mas-lab benchmark migrate examples/trip-planner/output/01-semantic-grouping

    # Explicit target
    mas-lab benchmark migrate ./output/my-run labs_root()/trip-planner/my-run

    # Preview without touching the filesystem
    mas-lab benchmark migrate ./output/my-run --dry-run
    """
    from mas.lab.benchmark.cli import migrate_command

    args = SimpleNamespace(
        source_dir=source_dir,
        target_dir=target_dir,
        no_symlink=no_symlink,
        dry_run=dry_run,
        mas_data_root=str(mas_data_root) if mas_data_root else None,
    )
    raise SystemExit(migrate_command(args))
