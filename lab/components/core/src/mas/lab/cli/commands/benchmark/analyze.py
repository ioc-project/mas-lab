#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab benchmark analyze`` command."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import click


@click.command("analyze")
@click.argument("benchmark_id")
@click.option("--experiment-yaml", type=Path, default=None,
              help="Path to experiment.yaml (auto-detected if omitted).")
@click.option("-o", "--output-dir", type=Path, default=None)
def analyze_cmd(benchmark_id: str, experiment_yaml: Path | None,
                output_dir: Path | None) -> None:
    """Regenerate statistics and plots from existing results."""
    from mas.lab.benchmark.cli import analyze_command

    args = SimpleNamespace(benchmark_id=benchmark_id,
                           experiment_yaml=experiment_yaml,
                           output_dir=output_dir)
    raise SystemExit(analyze_command(args))
