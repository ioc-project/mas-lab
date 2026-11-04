#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab benchmark step-info`` command."""
from __future__ import annotations

import json as _json
import textwrap

import click


@click.command("step-info")
@click.argument("step_type", required=False, default=None)
@click.option("--json-output", "json_out", is_flag=True, help="Output as JSON.")
def step_info_cmd(step_type: str | None, json_out: bool) -> None:
    """Show parameters, defaults, and artifacts for a pipeline step type.

    Without STEP_TYPE, lists all registered step types.

    \b
    Examples:
        mas-lab benchmark step-info
        mas-lab benchmark step-info export_otel
        mas-lab benchmark step-info export_otel --json-output
    """
    from mas.lab.benchmark.pipeline import get_step, list_steps

    registry = list_steps()

    if step_type is None:
        types = sorted(set(registry.keys()))
        if json_out:
            click.echo(_json.dumps(types, indent=2))
            return
        click.echo(f"Registered step types ({len(types)}):\n")
        for t in types:
            cls = registry[t]
            if cls is None:
                click.echo(f"  {t}")
                continue
            doc = (cls.__doc__ or "").strip().split("\n")[0].strip().rstrip(".")
            click.echo(f"  {t:40s}  {doc}")
        click.echo("\nRun: mas-lab benchmark step-info <type>  for detailed parameters.")
        return

    cls = get_step(step_type)
    if cls is None:
        click.secho(f"Unknown step type: {step_type!r}", fg="red")
        click.echo("Run 'mas-lab benchmark step-info' to list available types.")
        raise SystemExit(1)

    params = cls.PARAMS
    doc = textwrap.dedent(cls.__doc__ or "").strip()

    if json_out:
        param_list = []
        for p in params:
            param_list.append({
                "name": p.name,
                "type": p.type.__name__,
                "required": p.required,
                "default": None if p.required else p.default,
                "description": p.description,
            })
        click.echo(_json.dumps({
            "type": step_type,
            "class": cls.__name__,
            "params": param_list,
        }, indent=2))
        return

    click.echo()
    click.secho(f"Step type: {step_type}", bold=True)
    click.echo(f"Class:     {cls.__name__}")
    click.echo()

    if params:
        req_params  = [p for p in params if p.required]
        opt_params  = [p for p in params if not p.required]
        col_w = max((len(p.name) for p in params), default=12) + 2

        if req_params:
            click.secho("  Required parameters:", fg="red")
            for p in req_params:
                desc = textwrap.fill(p.description, width=70,
                                     subsequent_indent=" " * (col_w + 18))
                click.echo(f"    {p.name:{col_w}s}  {p.type.__name__:<8s}  {desc}")

        if opt_params:
            click.echo()
            click.secho("  Optional parameters (with defaults):", fg="green")
            for p in opt_params:
                default_str = p.default_repr()
                desc = textwrap.fill(p.description, width=70,
                                     subsequent_indent=" " * (col_w + 18))
                click.echo(f"    {p.name:{col_w}s}  {p.type.__name__:<8s}  "
                           f"[default: {default_str}]  {desc}")
    else:
        click.echo("  (No structured PARAMS declared — showing module docstring)\n")
        for line in doc.splitlines():
            click.echo(f"  {line}")

    manifest = cls.manifest()
    if manifest:
        click.echo()
        if manifest.inputs:
            click.secho("  Input artifacts:", fg="cyan")
            for a in manifest.inputs:
                req = "required" if "optional" not in a.kind else "optional"
                click.echo(f"    {a.name} ({req}): {a.description}")
        if manifest.outputs:
            click.secho("  Output artifacts:", fg="cyan")
            for a in manifest.outputs:
                suffix = f"  [{a.file_pattern}]" if a.file_pattern else ""
                click.echo(f"    {a.name}{suffix}: {a.description}")
    click.echo()
