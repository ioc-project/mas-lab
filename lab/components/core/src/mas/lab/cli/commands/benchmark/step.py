#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab benchmark step`` subcommands."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import click


@click.group("step")
def step_group() -> None:
    """Manage benchmark steps."""


@step_group.command("list")
@click.argument("benchmark_id")
@click.option("-o", "--output-dir", type=Path, default=None)
def step_list_cmd(benchmark_id: str, output_dir: Path | None) -> None:
    """List all steps in a benchmark."""
    from mas.lab.benchmark.cli import step_list_command

    args = SimpleNamespace(benchmark_id=benchmark_id, output_dir=output_dir)
    raise SystemExit(step_list_command(args))


@step_group.command("show")
@click.argument("benchmark_id")
@click.argument("step_id")
@click.option("-o", "--output-dir", type=Path, default=None)
def step_show_cmd(benchmark_id: str, step_id: str, output_dir: Path | None) -> None:
    """Show details of a specific step."""
    from mas.lab.benchmark.cli import step_show_command

    args = SimpleNamespace(benchmark_id=benchmark_id, step_id=step_id,
                           output_dir=output_dir)
    raise SystemExit(step_show_command(args))


@step_group.command("restart")
@click.argument("benchmark_id")
@click.argument("step_id")
@click.option("-o", "--output-dir", type=Path, default=None)
def step_restart_cmd(benchmark_id: str, step_id: str, output_dir: Path | None) -> None:
    """Restart a specific step."""
    from mas.lab.benchmark.cli import step_restart_command

    args = SimpleNamespace(benchmark_id=benchmark_id, step_id=step_id,
                           output_dir=output_dir)
    raise SystemExit(step_restart_command(args))
