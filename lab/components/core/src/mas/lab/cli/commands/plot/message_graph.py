#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab plot message_graph`` command."""
from __future__ import annotations

import sys
from pathlib import Path

import click

from ._common import default_output, resolve_source


@click.command("message-graph")
@click.argument("source", metavar="SOURCE")
@click.option(
    "--format", "fmt",
    type=click.Choice(["svg", "html"], case_sensitive=False),
    default="svg",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--theme",
    type=click.Choice(["auto", "light", "dark"], case_sensitive=False),
    default="auto",
    show_default=True,
    help="Colour theme: auto (follow OS), light, or dark.",
)
@click.option(
    "--hover/--no-hover",
    default=True,
    show_default=True,
    help="Attach hover tooltips (HTML mode) / native SVG title elements.",
)
@click.option(
    "--time-axis/--no-time-axis",
    default=True,
    show_default=True,
    help="Draw a horizontal time axis below the diagram lanes.",
)
@click.option(
    "--respect-timing/--no-respect-timing",
    default=True,
    show_default=True,
    help="Scale box widths proportionally to LLM call duration.",
)
@click.option(
    "--title",
    default="",
    show_default=False,
    help="Diagram title (default: run_id from kg metadata).",
)
@click.option(
    "--output", "-o",
    type=Path,
    default=None,
    metavar="FILE",
    help="Write output to FILE (default: <source-dir>/message-graph.<ext>).",
)
def message_graph_cmd(
    source: str,
    fmt: str,
    theme: str,
    hover: bool,
    time_axis: bool,
    respect_timing: bool,
    title: str,
    output: "Path | None",
) -> None:
    """Render a whole-run agent message-flow swimlane diagram.

    Shows agent lanes, iteration bands, delegation + return edges, and
    tool-call indicators.  Reads kg.json (preferred) or events.jsonl.

    SOURCE can be a file path (kg.json or events.jsonl), a lab shorthand,
    or a run_id.  See ``mas-lab plot --help`` for details.

    \b
    Examples:
      mas-lab plot message-graph path/to/kg.json -o flow.svg
      mas-lab plot message-graph path/to/kg.json --format html -o flow.html
      mas-lab plot message-graph cognitive-challenges/c4-smoke -o flow.svg
      mas-lab plot message-graph 20260224-062142-baseline-673d6359
    """
    try:
        from mas.lab.plots.kg_adapter import load_kg
        from mas.lab.plots.message_graph import plot_message_graph
    except ImportError as exc:
        click.echo(f"❌  Could not import message graph plotter: {exc}", err=True)
        sys.exit(1)

    resolved = resolve_source(source)
    resolved_path = Path(resolved).expanduser()

    # Accept kg.json directly or fall back to events.jsonl path
    if resolved_path.exists() and resolved_path.suffix == ".json":
        try:
            kg = load_kg(resolved_path)
        except Exception as exc:
            click.echo(f"❌  Could not load kg.json: {exc}", err=True)
            sys.exit(1)
    elif resolved_path.exists() and resolved_path.suffix == ".jsonl":
        # Derive kg.json from sibling directory
        kg_candidate = resolved_path.parent.parent / "kg.json"
        if not kg_candidate.exists():
            kg_candidate = resolved_path.parent / "kg.json"
        if kg_candidate.exists():
            try:
                kg = load_kg(kg_candidate)
            except Exception as exc:
                click.echo(f"❌  Could not load kg.json: {exc}", err=True)
                sys.exit(1)
        else:
            click.echo(
                "❌  message-graph requires a kg.json file.  "
                "Run the normalize step first to produce kg.json.",
                err=True,
            )
            sys.exit(1)
    elif resolved_path.is_dir():
        # Directory: look for kg.json inside
        candidates = list(resolved_path.rglob("kg.json"))
        if not candidates:
            click.echo(
                f"❌  No kg.json found under {resolved_path}",
                err=True,
            )
            sys.exit(1)
        kg_path = candidates[0]
        click.echo(f"ℹ️   Using {kg_path}", err=True)
        try:
            kg = load_kg(kg_path)
        except Exception as exc:
            click.echo(f"❌  Could not load kg.json: {exc}", err=True)
            sys.exit(1)
        resolved_path = kg_path
    else:
        from mas.lab import paths as lab_paths

        lab_dir = lab_paths.labs_root() / resolved
        if lab_dir.is_dir():
            candidates = list(lab_dir.rglob("kg.json"))
            if not candidates:
                click.echo(f"❌  No kg.json found under {lab_dir}", err=True)
                sys.exit(1)
            kg_path = candidates[0]
            click.echo(f"ℹ️   Using {kg_path}", err=True)
            try:
                kg = load_kg(kg_path)
            except Exception as exc:
                click.echo(f"❌  Could not load kg.json: {exc}", err=True)
                sys.exit(1)
            resolved_path = kg_path
        else:
            # Try run_id resolution via mas.lab.paths
            try:
                from mas.lab import paths as _paths
                kg_path = _paths.resolve_run_artifact(resolved, artifact="kg.json")
                kg = load_kg(kg_path)
                resolved_path = Path(kg_path)
            except Exception as exc:
                click.echo(f"❌  Could not resolve source: {exc}", err=True)
                sys.exit(1)

    if not title:
        title = kg.get("run_id") or kg.get("meta", {}).get("run_id") or ""

    content = plot_message_graph(
        kg, title=title, fmt=fmt, theme=theme, hover=hover,
        time_axis=time_axis, respect_timing=respect_timing,
    )

    if output is None:
        output = default_output(str(resolved_path), "message-graph", fmt)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    click.echo(f"✅  Written to {output}")

