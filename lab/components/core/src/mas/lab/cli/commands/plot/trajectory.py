#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab plot trajectory`` command."""
from __future__ import annotations

import sys
from pathlib import Path

import click

from ._common import default_output, resolve_source


@click.command("trajectory")
@click.argument("source", metavar="SOURCE")
@click.option(
    "--format", "fmt",
    type=click.Choice(["mermaid", "table", "html", "svg"], case_sensitive=False),
    default="mermaid",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--no-prompts", "no_prompts",
    is_flag=True,
    default=False,
    help="Omit task/prompt text from diagram edges.",
)
@click.option(
    "--output", "-o",
    type=Path,
    default=None,
    metavar="FILE",
    help="Write output to FILE (default: <source-dir>/trajectory.<ext>).",
)
@click.option(
    "--highlight", "highlights",
    multiple=True,
    metavar="CORR_OR_INDEX",
    help=(
        "Highlight a delegation (amber + ⚠️).  Repeat to flag multiple.  "
        "Each value is a correlation-id prefix (e.g. 'f19445b6') "
        "or a 1-based delegation index (e.g. '2')."
    ),
)
def trajectory_cmd(
    source: str,
    fmt: str,
    no_prompts: bool,
    output: Path | None,
    highlights: tuple[str, ...],
) -> None:
    """Generate an agent delegation-flow diagram.

    SOURCE can be a file path (``events.jsonl``), a lab shorthand,
    or a run_id.  See ``mas-lab plot --help`` for details.

    \b
    Examples:
      mas-lab plot trajectory path/to/events.jsonl
      mas-lab plot trajectory tutorials/t3-analysis/baseline/item1/r1 --format svg -o traj.svg
      mas-lab plot trajectory events.jsonl --format html --highlight 2
    """
    try:
        from mas.lab.plots.trajectory import load_trace, plot_trajectory
    except ImportError as exc:  # pragma: no cover
        click.echo(f"❌  Could not import mas.lab.plots: {exc}", err=True)
        sys.exit(1)

    resolved = resolve_source(source)
    try:
        events = load_trace(resolved)
    except FileNotFoundError as exc:
        click.echo(f"❌  {exc}", err=True)
        sys.exit(1)

    if not events:
        click.echo("⚠️  Trace is empty — no events loaded.", err=True)
        sys.exit(0)

    result = plot_trajectory(
        events,
        fmt=fmt,
        include_prompts=not no_prompts,
        highlights=list(highlights) if highlights else None,
    )

    if output is None:
        output = default_output(resolved, "trajectory", fmt)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(result, encoding="utf-8")
    click.echo(f"✅  Written to {output}")

