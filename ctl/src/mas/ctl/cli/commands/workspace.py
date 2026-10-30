#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""mas-ctl infra / flavour — list bundles from installed manifest libraries."""

from __future__ import annotations

import json
from pathlib import Path

import click
import yaml
from mas.ctl.cli.bind_flags import middleware_option
from mas.ctl.libraries.bundles import list_bundles, list_manifest_libraries

INFRA_SHOW_EPILOG = """
Examples:
  mas-ctl infra show --infra-ref arm.yaml --infra-ref standard:llm-cache
  mas-ctl infra show --json
  mas-ctl infra show mas.yaml --middleware standard:llm-cache
"""


@click.group()
def infra_group() -> None:
    """Infrastructure bundle discovery."""


@infra_group.command("list")
@click.option("-v", "--verbose", is_flag=True)
def infra_list(verbose: bool) -> None:
    """List infra bundles (alias: mas-ctl list-bundles)."""
    libs = list_manifest_libraries()
    if not libs:
        click.echo("No manifest libraries. Run: task install-all")
        return
    bundles = list_bundles(verbose=verbose)
    for b in bundles:
        if b.kind and "Infra" in b.kind or b.kind in ("LLMProxy", "ToolRegistry", "InfraBundle"):
            click.echo(f"{b.ref}  kind={b.kind}  {b.name}")


@infra_group.command("show", epilog=INFRA_SHOW_EPILOG)
@click.argument("manifest", required=False)
@click.option(
    "--infra-ref",
    "infra_refs",
    multiple=True,
    help="Infrastructure bundle ref (merged after workspace infra_refs)",
)
@click.option(
    "--runtime-ref",
    "runtime_refs",
    multiple=True,
    help="RuntimeEngine manifest ref (merged after workspace runtime_refs)",
)
@click.option(
    "--override",
    "overrides",
    multiple=True,
    metavar="PATH=VALUE",
    help="Schema-validated overlay override (repeatable; applied last).",
)
@middleware_option
@click.option("--json", "as_json", is_flag=True, help="Print the stable JSON document.")
def infra_show(
    manifest: str | None,
    infra_refs: tuple[str, ...],
    runtime_refs: tuple[str, ...],
    overrides: tuple[str, ...],
    middleware_refs: tuple[str, ...],
    as_json: bool,
) -> None:
    """Print each LLM and tool chain, and each agent's comm stack.

    Same --infra-ref / --runtime-ref / --override / --middleware flags as
    chat and run-mas. Output is bounded (32 destinations, 16 steps each).
    """
    from mas.ctl.infra.show import agents_from_manifest, infra_show_payload, render_infra_show
    from mas.ctl.session.infra_resolve import resolve_session_infra
    from mas.ctl.workspace.config import UserConfig, WorkspaceConfig

    start = Path(manifest).expanduser().resolve().parent if manifest else Path.cwd()
    workspace = WorkspaceConfig.load(start)
    workspace = workspace.with_cli_overrides(overrides)
    user = UserConfig.load()
    document: dict | None = None
    if manifest:
        loaded = yaml.safe_load(Path(manifest).expanduser().read_text(encoding="utf-8"))
        document = loaded if isinstance(loaded, dict) else None
    infra = resolve_session_infra(
        document,
        workspace,
        user,
        infra_refs_cli=infra_refs,
        overrides=overrides,
        runtime_refs_cli=runtime_refs,
        middleware_refs=middleware_refs,
        anchor=start,
        with_interceptors=True,
    )
    payload = infra_show_payload(infra, agents=agents_from_manifest(document))
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(render_infra_show(payload), nl=False)


@click.group()
def flavour_group() -> None:
    """Flavour manifests (deprecated — prefer deployment/v1)."""


@flavour_group.command("list")
def flavour_list() -> None:
    """List flavour YAML files from installed libraries."""
    try:
        from importlib.resources import as_file, files
    except ImportError:
        click.echo("No flavours (importlib.resources unavailable)")
        return
    found = False
    for lib_name, pkg in list_manifest_libraries().items():
        try:
            path = files(pkg) / "flavours"
            with as_file(path) as p:
                if not p.is_dir():
                    continue
                for yaml_file in sorted(p.glob("*.yaml")):
                    found = True
                    click.echo(f"{lib_name}:{yaml_file.stem}")
        except Exception:
            continue
    if not found:
        click.echo("No flavours found. Run: task install-all")
