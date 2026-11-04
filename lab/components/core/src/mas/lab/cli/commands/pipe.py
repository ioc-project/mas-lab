#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab pipe`` command group — processor pipeline.

Sub-commands
------------
run     Execute a pipeline (inline GStreamer syntax or YAML file).
list    List all registered processor elements.
schema  Export the element registry as a GUI-compatible JSON schema.

GStreamer syntax
---------------
Separate elements with ``!``.  Parameters use ``key=value`` tokens::

    mas-lab pipe run trajectory_loader trace=runs/.../events.jsonl ! multilevel_trajectory_plotter fmt=html

The ``!`` must be a separate shell token.  In interactive zsh, quote it or
use ``setopt NO_BANG_HIST``; in scripts and non-interactive shells it works
as-is.

Equivalent non-pipeline invocations::

    mas-lab run processor trajectory_loader trace=runs/.../events.jsonl
    # ... pipe result to next step ...

YAML file
---------
::

    mas-lab pipe run --file my-pipeline.yaml
    mas-lab pipe run --file my-pipeline.yaml --dry-run

Schema export (for GUI)
-----------------------
::

    mas-lab pipe schema --output schema.json
    mas-lab pipe schema --element multilevel_trajectory_plotter
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click

# ---------------------------------------------------------------------------
# Group
# ---------------------------------------------------------------------------

@click.group("pipe")
def pipe_group() -> None:
    """Processor pipeline.

    \b
    Chain elements with '!'::

        mas-lab pipe run trajectory_loader trace=runs/.../events.jsonl ! multilevel_trajectory_plotter fmt=html

    Save/load pipelines as YAML::

        mas-lab pipe run --file my-pipeline.yaml

    Export GUI schema::

        mas-lab pipe schema --output schema.json
    """


# ---------------------------------------------------------------------------
# pipe run
# ---------------------------------------------------------------------------

@pipe_group.command(
    "run",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("pipeline_tokens", nargs=-1, type=str)
@click.option(
    "--file", "-f", "pipeline_file",
    default=None, metavar="FILE",
    help="Load pipeline from a YAML file instead of inline tokens.",
)
@click.option(
    "--dry-run", is_flag=True, default=False,
    help="Print the execution plan without running anything.",
)
@click.option(
    "--strict", is_flag=True, default=False,
    help="Abort on artifact type mismatches between steps.",
)
@click.option(
    "--verbose", "-v", is_flag=True, default=False,
    help="Print per-step progress to stderr.",
)
@click.option(
    "--import", "extra_imports",
    multiple=True, metavar="MODULE",
    help="Import a Python module before resolving processors (registers plugins).",
)
def pipe_run_cmd(
    pipeline_tokens: tuple[str, ...],
    pipeline_file: str | None,
    dry_run: bool,
    strict: bool,
    verbose: bool,
    extra_imports: tuple[str, ...],
) -> None:
    """Run a pipeline (inline or from file).

    \b
    Inline syntax:
      mas-lab pipe run trajectory_loader trace=SESSION/events.jsonl ! trajectory_plotter_native fmt=svg

    From a YAML file:
      mas-lab pipe run --file my-pipeline.yaml

    Dry-run (inspect without executing):
      mas-lab pipe run trajectory_loader trace=xyz ! trajectory_plotter_native --dry-run

    \b
    Parameter syntax: key=value
      - Strings    : label=moderator
      - Integers   : width=80
      - Floats     : threshold=0.85
      - Booleans   : verbose=true
      - Positional : first token after element name maps to first required param
    """
    # ── extra imports ───────────────────────────────────────────────────────
    for mod in extra_imports:
        try:
            import importlib
            importlib.import_module(mod)
        except ImportError as exc:
            click.echo(f"Cannot import '{mod}': {exc}", err=True)
            sys.exit(1)

    # ── load pipeline ────────────────────────────────────────────────────────
    try:
        from mas.lab.pipe import Pipeline
    except ImportError as exc:
        click.echo(f"Cannot import mas.lab.pipe: {exc}", err=True)
        sys.exit(1)

    if pipeline_file:
        path = Path(pipeline_file)
        if not path.exists():
            click.echo(f"Pipeline file not found: {path}", err=True)
            sys.exit(1)
        try:
            pipeline = Pipeline.from_yaml(path)
        except Exception as exc:
            click.echo(f"Failed to load pipeline file: {exc}", err=True)
            sys.exit(1)
    elif pipeline_tokens:
        try:
            pipeline = Pipeline.from_tokens(list(pipeline_tokens))
        except ValueError as exc:
            click.echo(f"Pipeline syntax error: {exc}", err=True)
            click.echo(
                "\nExpected:  mas-lab pipe run element1 k=v ! element2 k=v",
                err=True,
            )
            sys.exit(1)
    else:
        click.echo(
            "Provide pipeline tokens or use --file.  "
            "Run  mas-lab pipe run --help  for syntax.",
            err=True,
        )
        sys.exit(1)

    # ── validate ─────────────────────────────────────────────────────────────
    issues = pipeline.validate(strict=strict)
    if issues:
        click.echo(click.style("Pipeline validation issues:", fg="yellow"), err=True)
        for issue in issues:
            click.echo(f"  ⚠ {issue}", err=True)
        if strict:
            sys.exit(1)

    # ── dry-run banner ───────────────────────────────────────────────────────
    if dry_run:
        click.echo(click.style("Pipeline (dry-run):", bold=True))
        click.echo(f"  {pipeline.to_inline()}")
        click.echo(f"  {len(pipeline.steps)} step(s)\n")
        pipeline.run(dry_run=True)
        return

    # ── run ──────────────────────────────────────────────────────────────────
    if verbose:
        click.echo(
            click.style(f"Running: {pipeline.to_inline()}", fg="blue"),
            err=True,
        )

    try:
        result = pipeline.run(verbose=verbose)
    except KeyError as exc:
        click.echo(f"Unknown element: {exc}", err=True)
        click.echo("Run  mas-lab pipe list  to see available elements.", err=True)
        sys.exit(1)
    except Exception as exc:
        click.echo(f"Pipeline failed: {exc}", err=True)
        if verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)

    if result is not None and verbose:
        click.echo(f"  → result: {result.kind if hasattr(result, 'kind') else type(result).__name__}", err=True)


# ---------------------------------------------------------------------------
# pipe list
# ---------------------------------------------------------------------------

@pipe_group.command("list")
@click.option(
    "--import", "extra_imports",
    multiple=True, metavar="MODULE",
    help="Import a Python module to register additional processors.",
)
def pipe_list_cmd(extra_imports: tuple[str, ...]) -> None:
    """List all registered pipeline elements.

    \b
    Examples:
      mas-lab pipe list
      mas-lab pipe list --import my_plugin.processors
    """
    for mod in extra_imports:
        try:
            import importlib
            importlib.import_module(mod)
        except ImportError as exc:
            click.echo(f"Cannot import '{mod}': {exc}", err=True)

    try:
        from mas.lab.processor import ParamDef, list_processors
    except ImportError as exc:
        click.echo(f"Cannot import mas.lab.processor: {exc}", err=True)
        sys.exit(1)

    # Ensure built-ins are loaded
    try:
        import mas.lab.plots as _  # noqa: F401
    except Exception:
        pass

    procs = list_processors()
    if not procs:
        click.echo("(no processors registered)")
        return

    click.echo(f"\n{'ELEMENT':<30} {'PRI':>3}  {'INPUT':<20} {'OUTPUT':<20}  DESCRIPTION")
    click.echo("─" * 100)
    for p in procs:
        prio     = getattr(p, "priority", 10)
        in_kind  = getattr(p, "input_kind",  "") or "—"
        out_kind = getattr(p, "output_kind", "") or "—"
        desc     = getattr(p, "description", "")
        click.echo(f"{p.name:<30} {prio:>3}  {in_kind:<20} {out_kind:<20}  {desc}")

        # Show params on next line if any
        params = [pp for pp in getattr(p, "params", []) if isinstance(pp, ParamDef)]
        if params:
            param_str = "  params: " + ", ".join(
                f"{pp.name}{'*' if pp.required else ''}={'?' if pp.default is None else pp.default}"
                for pp in params
            )
            click.echo(click.style(param_str, fg="bright_black"))

    click.echo(f"\n{len(procs)} element(s).  Params marked * are required.")
    click.echo()


# ---------------------------------------------------------------------------
# pipe schema
# ---------------------------------------------------------------------------

@pipe_group.command("schema")
@click.option(
    "--element", "element_name", default=None, metavar="ELEMENT",
    help="Emit schema for one element only.",
)
@click.option(
    "--output", "-o", "output", default="-", metavar="FILE|-",
    help="Write JSON to FILE instead of stdout.",
)
@click.option(
    "--format", "fmt", default="json",
    type=click.Choice(["json", "yaml"]),
    show_default=True,
    help="Output format.",
)
@click.option(
    "--import", "extra_imports",
    multiple=True, metavar="MODULE",
    help="Import a Python module to register additional processors.",
)
def pipe_schema_cmd(
    element_name: str | None,
    output: str,
    fmt: str,
    extra_imports: tuple[str, ...],
) -> None:
    """Export the element registry as a GUI-compatible schema.

    The JSON output describes every registered processor with its inputs,
    outputs, and parameter definitions — ready for consumption by n8n,
    Blender node graphs, or any visual pipeline editor.

    \b
    Examples:
      mas-lab pipe schema                          # all elements → stdout
      mas-lab pipe schema --element multilevel_trajectory_plotter     # one element
      mas-lab pipe schema --output schema.json     # write to file
      mas-lab pipe schema --format yaml            # YAML format
    """
    for mod in extra_imports:
        try:
            import importlib
            importlib.import_module(mod)
        except ImportError as exc:
            click.echo(f"Cannot import '{mod}': {exc}", err=True)

    try:
        from mas.lab.pipe import build_schema
    except ImportError as exc:
        click.echo(f"Cannot import mas.lab.pipe: {exc}", err=True)
        sys.exit(1)

    try:
        schema = build_schema(element_name)
    except KeyError as exc:
        click.echo(f"Element not found: {exc}", err=True)
        sys.exit(1)

    # Serialise
    if fmt == "yaml":
        try:
            import yaml as _yaml  # type: ignore
            text = _yaml.dump(schema, default_flow_style=False, allow_unicode=True)
        except ImportError:
            click.echo("PyYAML required for --format yaml.  uv add pyyaml", err=True)
            sys.exit(1)
    else:
        text = json.dumps(schema, indent=2, ensure_ascii=False) + "\n"

    if output == "-":
        click.echo(text, nl=False)
    else:
        Path(output).write_text(text, encoding="utf-8")
        click.echo(f"Schema written → {output}", err=True)


# ---------------------------------------------------------------------------
# pipe validate  (convenience — validate-only, no execution)
# ---------------------------------------------------------------------------

@pipe_group.command("validate")
@click.argument("pipeline_tokens", nargs=-1, type=str)
@click.option(
    "--file", "-f", "pipeline_file",
    default=None, metavar="FILE",
    help="Validate a YAML pipeline file.",
)
@click.option(
    "--strict", is_flag=True, default=False,
    help="Also check artifact type compatibility between steps.",
)
def pipe_validate_cmd(
    pipeline_tokens: tuple[str, ...],
    pipeline_file: str | None,
    strict: bool,
) -> None:
    """Validate a pipeline without running it.

    Exit code 0 = valid, 1 = issues found.

    \b
    Examples:
      mas-lab pipe validate trajectory_loader trace=xyz ! trajectory_plotter_native
      mas-lab pipe validate --file my-pipeline.yaml --strict
    """
    from mas.lab.pipe import Pipeline

    if pipeline_file:
        pipeline = Pipeline.from_yaml(Path(pipeline_file))
    elif pipeline_tokens:
        try:
            pipeline = Pipeline.from_tokens(list(pipeline_tokens))
        except ValueError as exc:
            click.echo(f"Syntax error: {exc}", err=True)
            sys.exit(1)
    else:
        click.echo("Provide pipeline tokens or --file.", err=True)
        sys.exit(1)

    issues = pipeline.validate(strict=strict)
    if issues:
        click.echo(click.style("Issues found:", fg="yellow"))
        for iss in issues:
            click.echo(f"  ✗ {iss}")
        sys.exit(1)
    else:
        steps = len(pipeline.steps)
        click.echo(click.style(f"✓ Pipeline valid  ({steps} step{'s' if steps != 1 else ''})", fg="green"))
