#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab benchmark import`` command."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import click


@click.command("import")
@click.argument("tarball", type=Path)
@click.option("--output-dir", type=Path, default=None,
              help="Directory to restore the benchmark output into "
                   "(default: labs_root()/benchmarks/<benchmark_id>).")
@click.option("--trace-cache", "trace_cache_dir", type=Path, default=None,
              help="Directory to restore trace-cache entries into "
                   "(default: trace_cache() or MAS_TRACE_CACHE).")
@click.option("--dry-run", is_flag=True, default=False,
              help="Show what would be extracted without touching the filesystem.")
def import_cmd(tarball: Path, output_dir: Path | None,
               trace_cache_dir: Path | None, dry_run: bool) -> None:
    """Restore a benchmark archive produced by ``benchmark export``.

    Extracts the benchmark output and trace-cache entries into the specified
    directories, then patches absolute paths inside metadata.yaml so the
    benchmark is immediately accessible via ``mas-lab benchmark show``.

    \b
    Examples
    --------
    # Restore to defaults (XDG data root)
    mas-lab benchmark import run.tar.gz

    # Restore to a temp directory for inspection / testing
    mas-lab benchmark import run.tar.gz \\
        --output-dir /tmp/test-bench \\
        --trace-cache /tmp/test-trace-cache

    # Preview without writing anything
    mas-lab benchmark import run.tar.gz --dry-run
    """
    from mas.lab.benchmark.cli import import_command

    args = SimpleNamespace(
        tarball=tarball,
        output_dir=output_dir,
        trace_cache_dir=trace_cache_dir,
        dry_run=dry_run,
    )
    raise SystemExit(import_command(args))
