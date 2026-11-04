#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab plot list`` command."""
from __future__ import annotations

from pathlib import Path

import click


@click.command("list")
@click.argument(
    "directory",
    metavar="DIR",
    default=".",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
)
@click.option(
    "--traces", "show_traces",
    is_flag=True,
    default=False,
    help="Also list events.jsonl trace files.",
)
def list_cmd(directory: Path, show_traces: bool) -> None:
    """List generated plot artefacts in DIR (defaults to cwd).

    Recursively scans DIR for HTML/SVG plots and optionally events.jsonl traces,
    grouped by sub-directory and plot type.

    \b
    Examples:
      mas-lab plot list
      mas-lab plot list ~/mas-data/trip-planner --traces
    """
    directory = directory.resolve()

    html_files: list[Path] = sorted(directory.rglob("*.html"))
    svg_files:  list[Path] = sorted(directory.rglob("*.svg"))
    trace_files: list[Path] = sorted(directory.rglob("events.jsonl")) if show_traces else []

    if not html_files and not svg_files and not trace_files:
        click.echo(f"No plot artefacts found under {directory}")
        return

    PREFIXES = [
        ("multilevel_",       "Multilevel DAG",        "⏱ "),
        ("trajectory_",       "Delegation Flow",       "↗ "),
        ("commflow_",         "Communication Graph",   "⬡ "),
    ]

    def classify(p: Path) -> tuple[str, str]:
        name = p.name
        for prefix, label, icon in PREFIXES:
            if name.startswith(prefix):
                return label, icon
        return "HTML Plot", "◉ "

    by_dir: dict[str, list[tuple[str, str, Path, int]]] = {}
    for f in html_files + svg_files + trace_files:
        rel_dir = str(f.parent.relative_to(directory)) if f.parent != directory else "."
        if rel_dir not in by_dir:
            by_dir[rel_dir] = []
        if f.suffix == ".html":
            label, icon = classify(f)
        elif f.suffix == ".svg":
            label, icon = "SVG Plot", "▣ "
        else:
            label, icon = "Trace (JSONL)", "📋 "
        size_kb = f.stat().st_size // 1024
        by_dir[rel_dir].append((label, icon, f, size_kb))

    total = sum(len(v) for v in by_dir.values())
    click.echo(f"\n📂  {directory}  ({total} artefact{'s' if total != 1 else ''})\n")

    for sub, items in sorted(by_dir.items()):
        click.echo(f"  {sub}/")
        for label, icon, path, size_kb in sorted(items, key=lambda x: x[2].name):
            rel = path.relative_to(directory)
            click.echo(f"    {icon} {rel.name:<45}  {label}  ({size_kb} KB)")
        click.echo()

