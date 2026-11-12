#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""mas-ctl infra / flavour — list bundles from installed manifest libraries."""

from __future__ import annotations

import json
from pathlib import Path

import click
import yaml
from mas.ctl.cli.bind_flags import control_option, middleware_option
from mas.ctl.libraries.bundles import TYPE_HELP, catalog_kinds, list_bundles, list_manifest_libraries
from mas.ctl.session.flavour import SUPPORTED_FLAVOURS

INFRA_SHOW_EPILOG = """
Examples:
  mas-ctl infra show standard:openai
  mas-ctl infra show standard:openai --pipeline
  mas-ctl infra show ./arm.yaml --json
"""

INFRA_RESOLVED_EPILOG = """
Examples:
  mas-ctl infra resolved --infra-ref arm.yaml --infra-ref standard:llm-cache
  mas-ctl infra resolved --json
  mas-ctl infra resolved mas.yaml --middleware standard:llm-cache
"""

INFRA_SHOW_ALIAS_WARNING = "warning: infra show is the catalog entry; use infra resolved for a merged run"

_SESSION_KINDS = frozenset({"MAS", "Agent"})


@click.group()
def infra_group() -> None:
    """Infrastructure bundle discovery."""


@infra_group.command("list")
@click.option("-v", "--verbose", is_flag=True)
@click.option(
    "--type",
    "type_token",
    default=None,
    metavar="KIND",
    help=f"Filter catalog kind ({TYPE_HELP}).",
)
def infra_list(verbose: bool, type_token: str | None) -> None:
    """List infra bundles (alias: mas-ctl list-bundles)."""
    libs = list_manifest_libraries()
    if not libs:
        click.echo("No manifest libraries. Run: task install-all")
        return
    try:
        kinds = catalog_kinds(type_token)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--type") from exc
    bundles = list_bundles(verbose=verbose)
    for b in bundles:
        if b.kind in kinds:
            click.echo(f"{b.ref}  kind={b.kind}  {b.name}")


def _view_flags(connections: bool, pipeline: bool) -> tuple[bool, bool]:
    if connections or pipeline:
        return connections, pipeline
    return True, True


def _emit_payload(payload: dict, *, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    from mas.ctl.infra.show import render_infra_show

    click.echo(render_infra_show(payload), nl=False)


def _document_kind(path: Path) -> str:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return ""
    if not isinstance(loaded, dict):
        return ""
    return str(loaded.get("kind") or "")


def _use_resolved_alias(
    ref: str | None,
    *,
    infra_refs: tuple[str, ...],
    runtime_refs: tuple[str, ...],
    overrides: tuple[str, ...],
    middleware_refs: tuple[str, ...],
    controls: tuple[str, ...],
    flavour: str | None,
) -> bool:
    if infra_refs or runtime_refs or overrides or middleware_refs or controls or flavour:
        return True
    if not ref:
        return True
    path = Path(ref).expanduser()
    if path.is_file():
        return _document_kind(path) in _SESSION_KINDS
    return False


def _run_infra_resolved(
    manifest: str | None,
    *,
    infra_refs: tuple[str, ...],
    runtime_refs: tuple[str, ...],
    overrides: tuple[str, ...],
    middleware_refs: tuple[str, ...],
    controls: tuple[str, ...],
    flavour: str | None,
    as_json: bool,
    sections: tuple[str, ...],
    connections: bool,
    pipeline: bool,
) -> None:
    from mas.ctl.infra.bind import bind_flavour
    from mas.ctl.infra.resolve import InfraResolveError
    from mas.ctl.infra.show import agents_from_manifest, document_agent_ids, infra_show_payload
    from mas.ctl.session.flavour import flavour_argument, resolve_session_flavour
    from mas.ctl.session.infra_resolve import resolve_session_infra
    from mas.ctl.workspace.config import UserConfig, WorkspaceConfig
    from mas.library.standard.lib.stack import StackError

    flavour = flavour_argument(flavour)
    start = Path(manifest).expanduser().resolve().parent if manifest else Path.cwd()
    workspace = WorkspaceConfig.load(start)
    workspace = workspace.with_cli_overrides(overrides)
    user = UserConfig.load()
    document: dict | None = None
    if manifest:
        loaded = yaml.safe_load(Path(manifest).expanduser().read_text(encoding="utf-8"))
        document = loaded if isinstance(loaded, dict) else None
    connections, pipeline = _view_flags(connections, pipeline)
    try:
        flavour_spec = resolve_session_flavour(flavour, overrides=overrides, controls=controls, search_dirs=[start])
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
        ids = document_agent_ids(document) if document else []
        if document:
            from mas.ctl.infra.bind import bind_session

            infra = bind_session(infra, flavour_spec, agent_ids=ids)
        else:
            infra = bind_flavour(infra, flavour_spec, agent_ids=None)
        from mas.ctl.session.llm_retry_env import llm_http_retry_overrides

        spec = document.get("spec") if isinstance(document, dict) else None
        retry_overlay = llm_http_retry_overrides()
        payload = infra_show_payload(
            infra,
            agents=agents_from_manifest(document, infra=infra),
            sections=sections,
            connections=connections,
            pipeline=pipeline,
            session=True,
            spec=spec if isinstance(spec, dict) else None,
            retry_overlay=retry_overlay or None,
        )
    except (ValueError, InfraResolveError, StackError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit_payload(payload, as_json=as_json)


@infra_group.command("show", epilog=INFRA_SHOW_EPILOG)
@click.argument("ref", required=False)
@click.option("--connections", "connections_only", is_flag=True, help="Print destinations only.")
@click.option("--pipeline", "pipeline_only", is_flag=True, help="Print pipeline steps only.")
@click.option("--json", "as_json", is_flag=True, help="Print the stable JSON document.")
@click.option("--infra-ref", "infra_refs", multiple=True, hidden=True)
@click.option("--runtime-ref", "runtime_refs", multiple=True, hidden=True)
@click.option("--override", "overrides", multiple=True, hidden=True)
@click.option("--middleware", "middleware_refs", multiple=True, hidden=True)
@click.option("--control", "controls", multiple=True, hidden=True)
@click.option("--flavour", default=None, hidden=True)
def infra_show(
    ref: str | None,
    connections_only: bool,
    pipeline_only: bool,
    as_json: bool,
    infra_refs: tuple[str, ...],
    runtime_refs: tuple[str, ...],
    overrides: tuple[str, ...],
    middleware_refs: tuple[str, ...],
    controls: tuple[str, ...],
    flavour: str | None,
) -> None:
    """Print one catalog ref: connection fields and its pipeline.

    REF is a library ref (standard:openai) or a path to an infra YAML.
    For a merged run, use infra resolved.
    """
    if _use_resolved_alias(
        ref,
        infra_refs=infra_refs,
        runtime_refs=runtime_refs,
        overrides=overrides,
        middleware_refs=middleware_refs,
        controls=controls,
        flavour=flavour,
    ):
        click.echo(INFRA_SHOW_ALIAS_WARNING, err=True)
        _run_infra_resolved(
            ref,
            infra_refs=infra_refs,
            runtime_refs=runtime_refs,
            overrides=overrides,
            middleware_refs=middleware_refs,
            controls=controls,
            flavour=flavour,
            as_json=as_json,
            sections=(),
            connections=connections_only,
            pipeline=pipeline_only,
        )
        return
    from mas.ctl.infra.resolve import InfraResolveError, resolve_infra_refs
    from mas.ctl.infra.show import catalog_show_payload
    from mas.ctl.workspace.config import WorkspaceConfig
    from mas.library.standard.lib.stack import StackError

    start = Path(ref).expanduser().resolve().parent if Path(ref).expanduser().is_file() else Path.cwd()
    workspace = WorkspaceConfig.load(start)
    connections, pipeline = _view_flags(connections_only, pipeline_only)
    try:
        infra = resolve_infra_refs([ref], anchor=start, workspace=workspace, catalog=True)
        payload = catalog_show_payload(infra, connections=connections, pipeline=pipeline)
    except (ValueError, InfraResolveError, StackError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit_payload(payload, as_json=as_json)


@infra_group.command("resolved", epilog=INFRA_RESOLVED_EPILOG)
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
@control_option
@click.option(
    "--flavour",
    default=None,
    help="Flavour: bundled name local or prod, or a Flavour YAML path. Omitted: local.",
)
@click.option(
    "--type",
    "sections",
    multiple=True,
    type=click.Choice(["llm", "tool", "agents"], case_sensitive=False),
    help="Repeatable. Print only these sections.",
)
@click.option("--connections", "connections_only", is_flag=True, help="Print destinations only.")
@click.option("--pipeline", "pipeline_only", is_flag=True, help="Print pipeline steps only.")
@click.option("--json", "as_json", is_flag=True, help="Print the stable JSON document.")
def infra_resolved(
    manifest: str | None,
    infra_refs: tuple[str, ...],
    runtime_refs: tuple[str, ...],
    overrides: tuple[str, ...],
    middleware_refs: tuple[str, ...],
    controls: tuple[str, ...],
    flavour: str | None,
    sections: tuple[str, ...],
    connections_only: bool,
    pipeline_only: bool,
    as_json: bool,
) -> None:
    """Print the pipeline a session wraps: each LLM and tool chain, and each agent's comm.

    Same wrap as chat and run-mas (default retry, leftover spec.control).
    Same --infra-ref / --runtime-ref / --override / --middleware flags.
    Output is bounded (32 destinations, 16 steps each).
    """
    _run_infra_resolved(
        manifest,
        infra_refs=infra_refs,
        runtime_refs=runtime_refs,
        overrides=overrides,
        middleware_refs=middleware_refs,
        controls=controls,
        flavour=flavour,
        as_json=as_json,
        sections=sections,
        connections=connections_only,
        pipeline=pipeline_only,
    )


FLAVOUR_LIST_WARNING = (
    "warning: mas-ctl flavour list is deprecated; pass --flavour PATH to a Flavour file, "
    f"or one of the bundled names ({', '.join(SUPPORTED_FLAVOURS)})"
)


@click.group()
def flavour_group() -> None:
    """Flavour manifests."""


@flavour_group.command("list")
def flavour_list() -> None:
    """List flavour YAML files from installed libraries (deprecated: use --flavour PATH or a bundled name)."""
    click.echo(FLAVOUR_LIST_WARNING, err=True)
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
