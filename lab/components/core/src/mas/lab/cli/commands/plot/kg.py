#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab plot kg`` command."""
from __future__ import annotations

import sys
from pathlib import Path

import click

from ._common import default_output, resolve_source


@click.command("kg")
@click.argument("source", metavar="SOURCE")
@click.option(
    "--output", "-o",
    type=Path,
    default=None,
    metavar="FILE",
    help="Write HTML to FILE instead of opening the browser.",
)
@click.option("--title", default="", help="Widget title shown in the toolbar.")
@click.option(
    "--layout",
    type=click.Choice(["dagre", "dagre-lr", "cose-bilkent", "cose"], case_sensitive=False),
    default="dagre",
    show_default=True,
    help="Cytoscape.js layout algorithm.",
)
@click.option(
    "--panel",
    type=click.Choice(["open", "closed"], case_sensitive=False),
    default="open",
    show_default=True,
    help="Initial state of the left filter panel.",
)
@click.option(
    "--max-nodes", type=int, default=300, show_default=True,
    help="Maximum number of nodes to render.",
)
@click.option(
    "--no-open", is_flag=True, default=False,
    help="Write HTML to --output without opening the browser.",
)
def kg_cmd(
    source: str,
    output: "Path | None",
    title: str,
    layout: str,
    panel: str,
    max_nodes: int,
    no_open: bool,
) -> None:
    """Open an interactive knowledge-graph explorer in the browser.

    SOURCE is a kg.json file path, lab shorthand, or run_id.

    \b
    Examples:
      mas-lab plot kg output/kg.json
      mas-lab plot kg output/kg.json --layout cose-bilkent --panel closed
      mas-lab plot kg output/kg.json -o kg_view.html --no-open
      mas-lab plot kg tutorials/t3-analysis/baseline/item1/r1
    """
    try:
        from mas.lab.plots.kg_widget import build_standalone_html, serve_and_open
    except ImportError as exc:
        click.echo(f"❌  Could not import kg_widget: {exc}", err=True)
        sys.exit(1)

    resolved = resolve_source(source)
    resolved_path = Path(resolved).expanduser()

    if not resolved_path.exists():
        click.echo(f"❌  File not found: {resolved}", err=True)
        sys.exit(1)

    try:
        import json as _json
        kg_data = _json.loads(resolved_path.read_text(encoding="utf-8"))
    except Exception as exc:
        click.echo(f"❌  Could not parse KG JSON: {exc}", err=True)
        sys.exit(1)

    _title = title or resolved_path.stem
    opts = {"panelMode": panel, "layout": layout, "maxNodes": max_nodes, "title": _title}

    if output is not None or no_open:
        if output is None:
            output = default_output(resolved, "kg", "html")
        output.parent.mkdir(parents=True, exist_ok=True)
        html = build_standalone_html(kg_data, title=_title, opts=opts)
        output.write_text(html, encoding="utf-8")
        click.echo(f"✅  Written to {output}")
    else:
        serve_and_open(kg_data, title=_title, opts=opts)
