#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab plot topology`` command."""

from __future__ import annotations

import sys
from pathlib import Path

import click


@click.command("topology")
@click.argument("mas_yaml", type=click.Path(exists=True, dir_okay=False, path_type=Path), metavar="MAS_YAML")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["svg", "html"], case_sensitive=False),
    default="svg",
    show_default=True,
    help="Output format.",
)
@click.option("--title", default=None, help="Diagram title (default: the MAS name; '' for none).")
@click.option(
    "--entry-label",
    default="input",
    show_default=True,
    help="Word shown next to the entry marker, for example 'incident'.",
)
@click.option(
    "--output",
    "-o",
    type=Path,
    default=None,
    metavar="FILE",
    help="Write output to FILE (default: <mas.yaml dir>/topology.<ext>).",
)
def topology_cmd(mas_yaml: Path, fmt: str, title: "str | None", entry_label: str, output: "Path | None") -> None:
    """Draw the agents of a MAS, their skills and tools, and who talks to whom.

    Reads ``mas.yaml`` and the agent files it references; no trace is needed.

    \b
    Examples:
      mas-lab plot topology app/mas.yaml -o app.svg
      mas-lab plot topology app/mas.yaml --format html --entry-label incident
    """
    from mas.lab.plots.topology import plot_topology

    output = output or mas_yaml.parent / f"topology.{fmt}"
    try:
        text = plot_topology(mas_yaml, fmt=fmt.lower(), title=title, entry_label=entry_label)
    except (OSError, ValueError) as exc:
        click.echo(f"❌  {exc}", err=True)
        sys.exit(1)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    click.echo(f"✅  Written to {output}")
