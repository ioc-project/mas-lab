#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab plot context_provenance`` command."""
from __future__ import annotations

import sys
from pathlib import Path

import click

from ._common import default_output, resolve_source


@click.command("context-provenance")
@click.argument("source", metavar="SOURCE")
@click.option(
    "--format", "fmt",
    type=click.Choice(["svg", "html"], case_sensitive=False),
    default="html",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--theme",
    type=click.Choice(["auto", "light", "dark"], case_sensitive=False),
    default="auto",
    show_default=True,
    help="Colour theme.",
)
@click.option(
    "--hover/--no-hover",
    default=True,
    show_default=True,
    help="Attach hover tooltips to SVG elements.",
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
    help="Write output to FILE (default: <source-dir>/context-provenance.<ext>).",
)
def context_provenance_cmd(
    source: str,
    fmt: str,
    theme: str,
    hover: bool,
    title: str,
    output: "Path | None",
) -> None:
    """Render a multilevel context-provenance diagram from kg.json.

    Shows, for each LLM call, which agents and tools contributed context
    to its input — with colour-coded contributor chips and context-flow
    arrows between agent lanes.

    SOURCE accepts the same formats as message-graph.

    \b
    Examples:
      mas-lab plot context-provenance path/to/kg.json -o cp.html
      mas-lab plot context-provenance cognitive-challenges/c4-smoke --format html
    """
    try:
        from mas.lab.plots.kg_adapter import load_kg
        from mas.lab.plots.multilevel_context_provenance import (
            plot_multilevel_context_provenance,
        )
    except ImportError as exc:
        click.echo(f"\u274c  Could not import context provenance plotter: {exc}", err=True)
        sys.exit(1)

    # --- source resolution (same logic as message-graph) ---
    resolved = resolve_source(source)
    resolved_path = Path(resolved).expanduser()

    def _load_kg_from_path(p: Path):
        try:
            return load_kg(p)
        except Exception as exc:
            click.echo(f"\u274c  Could not load kg.json: {exc}", err=True)
            sys.exit(1)

    if resolved_path.exists() and resolved_path.suffix == ".json":
        kg = _load_kg_from_path(resolved_path)
    elif resolved_path.is_dir():
        candidates = list(resolved_path.rglob("kg.json"))
        if not candidates:
            click.echo(f"\u274c  No kg.json found under {resolved_path}", err=True)
            sys.exit(1)
        kg_path = candidates[0]
        click.echo(f"\u2139\ufe0f   Using {kg_path}", err=True)
        kg = _load_kg_from_path(kg_path)
        resolved_path = kg_path
    else:
        from mas.lab import paths as lab_paths

        lab_dir = lab_paths.labs_root() / resolved
        if lab_dir.is_dir():
            candidates = list(lab_dir.rglob("kg.json"))
            if not candidates:
                click.echo(f"\u274c  No kg.json found under {lab_dir}", err=True)
                sys.exit(1)
            kg_path = candidates[0]
            click.echo(f"\u2139\ufe0f   Using {kg_path}", err=True)
            kg = _load_kg_from_path(kg_path)
            resolved_path = kg_path
        else:
            try:
                from mas.lab import paths as _paths
                kg_path_str = _paths.resolve_run_artifact(resolved, artifact="kg.json")
                kg = _load_kg_from_path(Path(kg_path_str))
                resolved_path = Path(kg_path_str)
            except Exception as exc:
                click.echo(f"\u274c  Could not resolve source: {exc}", err=True)
                sys.exit(1)

    if not title:
        title = kg.get("run_id") or kg.get("meta", {}).get("run_id") or ""

    content = plot_multilevel_context_provenance(
        kg, title=title, fmt=fmt, theme=theme, hover=hover
    )

    if output is None:
        output = default_output(str(resolved_path), "context-provenance", fmt)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    click.echo(f"\u2705  Written to {output}")

