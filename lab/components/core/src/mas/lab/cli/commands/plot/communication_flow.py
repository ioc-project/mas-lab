#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab plot communication_flow`` command."""
from __future__ import annotations

import sys
from pathlib import Path

import click

from ._common import default_output, resolve_source


@click.command("communication-flow")
@click.argument("source", metavar="SOURCE")
@click.option(
    "--format", "fmt",
    type=click.Choice(["html", "mermaid"], case_sensitive=False),
    default="html",
    show_default=True,
    help="Output format.",
)
@click.option("--title", default="MAS Agent Communication Flow", show_default=True,
              help="Diagram title.")
@click.option(
    "--output", "-o",
    type=Path,
    default=None,
    metavar="FILE",
    help="Write output to FILE (default: <source-dir>/communication-flow.<ext>).",
)
def communication_flow_cmd(
    source: str,
    fmt: str,
    title: str,
    output: "Path | None",
) -> None:
    """Render an agent-to-agent communication flow diagram.

    SOURCE can be a file path (kg.json or events.jsonl), a lab shorthand,
    or a run_id.  See ``mas-lab plot --help`` for details.

    \b
    Examples:
      mas-lab plot communication-flow path/to/kg.json -o flow.html
      mas-lab plot communication-flow tutorials/t3-analysis/baseline/item1/r1
      mas-lab plot communication-flow tutorials/t3-analysis --format mermaid
    """
    try:
        from mas.lab.artifacts import Trajectory
        from mas.lab.plots.communication_flow import CommunicationFlowPlotter
    except ImportError as exc:
        click.echo(f"❌  Could not import communication flow plotter: {exc}", err=True)
        sys.exit(1)

    resolved = resolve_source(source)
    resolved_path = Path(resolved).expanduser()

    if output is None:
        output = default_output(resolved, "communication-flow", fmt)
    output.parent.mkdir(parents=True, exist_ok=True)

    proc = CommunicationFlowPlotter()
    try:
        result = proc.process(  # noqa: F841
            Trajectory(path=resolved_path if resolved_path.exists() else None,
                       run_id=resolved),
            fmt=fmt,
            title=title,
            output=output,
        )
    except (FileNotFoundError, ValueError) as exc:
        click.echo(f"❌  {exc}", err=True)
        sys.exit(1)

    click.echo(f"✅  Written to {output}")

