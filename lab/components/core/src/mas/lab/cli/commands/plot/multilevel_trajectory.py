#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab plot multilevel_trajectory`` command."""
from __future__ import annotations

import sys
from pathlib import Path

import click

from ._common import default_output, resolve_source


@click.command("multilevel-trajectory")
@click.argument("source", metavar="SOURCE")
@click.option(
    "--format", "fmt",
    type=click.Choice(["html", "svg"], case_sensitive=False),
    default="html",
    show_default=True,
    help="Output format.",
)
@click.option("--title", default="MAS Multilevel Trajectory", show_default=True,
              help="Diagram title.")
@click.option(
    "--width-mode", "width_mode",
    type=click.Choice(["fixed", "proportional", "log"], case_sensitive=False),
    default="log",
    show_default=True,
    help="Column-width strategy.",
)
@click.option("--no-user-actors", "no_user_actors", is_flag=True, default=False,
              help="Suppress person-icon user nodes.")
@click.option(
    "--output", "-o",
    type=Path,
    default=None,
    metavar="FILE",
    help="Write output to FILE (default: <source-dir>/multilevel-trajectory.<ext>).",
)
def multilevel_trajectory_cmd(
    source: str,
    fmt: str,
    title: str,
    width_mode: str,
    no_user_actors: bool,
    output: "Path | None",
) -> None:
    """Render a multilevel MAS swimlane diagram.

    SOURCE can be a file path (``events.jsonl``), a lab shorthand,
    or a run_id.  See ``mas-lab plot --help`` for details.

    \b
    Examples:
      mas-lab plot multilevel-trajectory path/to/events.jsonl -o swimlane.html
      mas-lab plot multilevel-trajectory tutorials/t3-analysis -o out.html
      mas-lab plot multilevel-trajectory tutorials/t3-analysis/baseline/item1/r1 --format svg
    """
    try:
        from mas.lab.artifacts import Trajectory
        from mas.lab.plots.multilevel_trajectory_processor import MultilevelTrajectoryPlotter
    except ImportError as exc:
        click.echo(f"❌  Could not import multilevel trajectory processor: {exc}", err=True)
        sys.exit(1)

    resolved = resolve_source(source)
    resolved_path = Path(resolved).expanduser()

    if output is None:
        output = default_output(resolved, "multilevel-trajectory", fmt)
    output.parent.mkdir(parents=True, exist_ok=True)

    proc = MultilevelTrajectoryPlotter()
    try:
        result = proc.process(  # noqa: F841
            Trajectory(path=resolved_path if resolved_path.exists() else None,
                       run_id=resolved),
            fmt=fmt,
            title=title,
            width_mode=width_mode,
            show_user_actors=not no_user_actors,
            output=output,
        )
    except (FileNotFoundError, ValueError) as exc:
        click.echo(f"❌  {exc}", err=True)
        sys.exit(1)

    click.echo(f"✅  Written to {output}")

