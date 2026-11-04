#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab benchmark export`` command."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import click


@click.command("export")
@click.argument("benchmark_id")
@click.option("-o", "--output", type=Path, default=None,
              help="Output .tar.gz path (default: <benchmark_id>.tar.gz in cwd).")
@click.option("--no-trace-cache", is_flag=True, default=False,
              help="Exclude trace-cache entries from the archive (smaller, not re-runnable).")
@click.option("--output-dir", type=Path, default=None,
              help="Benchmark runs root (default: auto-detect).")
@click.option("--trace-cache", "trace_cache_dir", type=Path, default=None,
              help="Trace-cache directory to read from (default: $XDG_CACHE_HOME/mas/traces).")
def export_cmd(benchmark_id: str, output: Path | None, no_trace_cache: bool,
               output_dir: Path | None, trace_cache_dir: Path | None) -> None:
    """Pack a benchmark run into a portable .tar.gz archive.

    The archive includes the benchmark output directory (metadata.yaml,
    results.csv, plots/, traces/) and, unless --no-trace-cache is set, the
    referenced trace-cache entries.

    The archive also embeds a MANIFEST.json so that ``benchmark import`` can
    restore everything to custom directories.

    \b
    Examples
    --------
    mas-lab benchmark export abc123                 # → abc123.tar.gz in cwd
    mas-lab benchmark export abc123 -o /tmp/run.tgz
    mas-lab benchmark export abc123 --no-trace-cache  # output dir only
    """
    from mas.lab.benchmark.cli import export_command

    args = SimpleNamespace(
        benchmark_id=benchmark_id,
        output=output,
        include_trace_cache=not no_trace_cache,
        output_dir=output_dir,
        trace_cache_dir=trace_cache_dir,
    )
    raise SystemExit(export_command(args))
