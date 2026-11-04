#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab benchmark show`` command."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import click


@click.command("show")
@click.argument("target", default=None, required=False)
@click.argument("what", default="info",
                type=click.Choice(["info", "plots", "events-trace", "otel-traces"]),
                required=False)
@click.option("-r", "--recursive", is_flag=True, default=False,
              help="Show a lab or experiment directory as a hierarchical tree. "
                   "TARGET must be a path to a lab directory or experiment YAML.")
@click.option("-o", "--output-dir", type=Path, default=None)
@click.option("--trace-cache", "trace_cache_dir", type=Path, default=None,
              help="Override trace-cache directory used for cache coverage display "
                   "(CLI > MAS_TRACE_CACHE > $XDG_CACHE_HOME/mas/traces).")
@click.option("--format", "fmt",
              type=click.Choice(["text","json","yaml"]),
              default="text", show_default=True)
@click.option("-v", "--verbose", count=True,
              help="-v/-vv: reserved  "
                   "-vvv: show pipeline DAG with artifact lineage")
@click.option("-d", "--depth", "depth",
              type=click.Choice(["exp", "scenario", "item", "run"], case_sensitive=False),
              default=None, metavar="LEVEL",
              help="Limit tree depth (requires -r).  "
                   "exp: experiment headers only  "
                   "scenario: + scenarios with counts (default)  "
                   "item: + expanded item list  "
                   "run: + per-run status")
@click.option("--scenario", default=None, metavar="SCENARIO",
              help="Filter tree to a specific scenario ID (requires --recursive).")
@click.option("--item", default=None, metavar="ITEM",
              help="Filter tree to a specific dataset item ID (requires --recursive).")
@click.option("--run", "run_idx", type=int, default=None, metavar="N",
              help="Filter tree to a specific run index (requires --recursive).")
def show_cmd(target: str, what: str, recursive: bool, output_dir: Path | None,
             trace_cache_dir: Path | None, fmt: str, verbose: int,
             depth: str | None,
             scenario: str | None, item: str | None, run_idx: int | None) -> None:
    """Show benchmark run details, or display a lab/experiment tree.

    TARGET is a benchmark ID (full/short UUID or 'last'), or a path to a lab
    directory / experiment YAML when used with --recursive (-r).

    WHAT can be one of: info (default), plots, events-trace, otel-traces.

    \b
    Tree mode (--recursive / -r):
      mas-lab benchmark show labs/design-space.lab/ -r
      mas-lab benchmark show labs/design-space.lab/ -r -d exp         # experiments only
      mas-lab benchmark show labs/design-space.lab/ -r -d item        # expand items
      mas-lab benchmark show labs/design-space.lab/01-design-patterns/ -r
      mas-lab benchmark show labs/design-space.lab/ -r --scenario pattern-cot
      mas-lab benchmark show labs/design-space.lab/ -r --item analysis-1 --run 2
      mas-lab benchmark show -r 318222ed   # look up artifact by short id
      mas-lab benchmark artifact list last
      mas-lab benchmark artifact list labs/design-space.lab/02-topologies/experiment.yaml
      mas-lab benchmark artifact show 318222ed
    """
    if recursive:
        import re as _re
        if target and _re.fullmatch(r'[0-9a-fA-F]{8}', target):
            from mas.lab.benchmark.cli import show_artifact_by_id_command
            raise SystemExit(show_artifact_by_id_command(
                SimpleNamespace(artifact_id=target)
            ))
        _target_path = Path(target) if target else Path.cwd()
        if not _target_path.exists():
            raise click.ClickException(
                f"Path not found: {_target_path}  (--recursive requires a lab or experiment path)"
            )
        from mas.lab.benchmark.cli import show_lab_tree_command
        args = SimpleNamespace(
            target=_target_path,
            verbose=verbose,
            scenario=scenario,
            item=item,
            run_idx=run_idx,
            output_dir=output_dir,
            depth=depth,
        )
        raise SystemExit(show_lab_tree_command(args))

    if not target:
        raise click.UsageError(
            "TARGET is required in non-recursive mode (a benchmark ID or 'last')."
        )
    from mas.lab.benchmark.cli import show_command

    args = SimpleNamespace(benchmark_id=target, what=what,
                           output_dir=output_dir, trace_cache_dir=trace_cache_dir,
                           format=fmt, verbose=bool(verbose))
    raise SystemExit(show_command(args))
