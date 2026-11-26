#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""mas-ctl hardening: list the named hardening checks and run them over a session."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click
import yaml
from mas.ctl.cli.bind_flags import control_option, middleware_option
from mas.ctl.hardening import BY_ID, CHECKS, DEFAULT, PRODUCTION, HardeningError, Report
from mas.ctl.hardening.collect import from_session
from mas.ctl.hardening.posture import evaluate, posture_of
from mas.ctl.infra.identities import agency_row_id
from mas.ctl.manifest.agency_groups import AgencyGroupError, group_members, loaded_session_mas
from mas.runtime.spec.yaml_errors import safe_yaml_load, yaml_error_text

CHECK_EPILOG = """
Exit status: 0 when no check failed, or under the default posture (advisory: failures are printed,
never block); 1 when the production posture has a failed check or the session cannot be resolved.

Examples:
  mas-ctl hardening check agent.yaml
  mas-ctl hardening check agent.yaml --profile production --flavour prod --json
  mas-ctl hardening check mas.yaml --flavour ./deploy-flavour.yaml --infra-ref ./infra.yaml
"""


@click.group("hardening")
def hardening_group() -> None:
    """Named hardening checks and the production posture."""


@hardening_group.command("list")
@click.option("--json", "as_json", is_flag=True, help="Print the checks as JSON.")
def hardening_list(as_json: bool) -> None:
    """List every check: id, severity, scope, rationale and fix."""
    rows = [
        {"id": c.id, "severity": c.severity, "scope": c.scope, "rationale": c.rationale, "fix": c.fix} for c in CHECKS
    ]
    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return
    for row in rows:
        click.echo(f"{row['id']}  [{row['severity']}, {row['scope']}]")
        click.echo(f"  why: {row['rationale']}")
        click.echo(f"  fix: {row['fix']}")


def _agent_specs(document: dict[str, Any], manifest: Path) -> dict[str, dict[str, Any]]:
    from mas.runtime.spec.source import load_yaml_file, resolve_ref_with_search

    spec = document.get("spec") if isinstance(document.get("spec"), dict) else {}
    if str(document.get("kind") or "") != "MAS":
        name = str((document.get("metadata") or {}).get("name") or manifest.stem)
        return {name: dict(spec)}
    out: dict[str, dict[str, Any]] = {}
    agency = spec.get("agency") if isinstance(spec.get("agency"), dict) else {}
    for row in agency.get("agents") or []:
        if not isinstance(row, dict):
            continue
        aid = agency_row_id(row)
        if not aid:
            continue
        if str(row.get("kind") or "").lower() == "agent" and isinstance(row.get("spec"), dict):
            out[aid] = dict(row.get("spec") or {})
            continue
        if not row.get("ref"):
            continue
        loaded = load_yaml_file(resolve_ref_with_search(str(row["ref"]), manifest.parent))
        if isinstance(loaded, dict):
            out[aid] = dict(loaded.get("spec") or {})
    return out


def _session_report(
    manifest: str,
    *,
    flavour: str | None,
    infra_refs: tuple[str, ...],
    runtime_refs: tuple[str, ...],
    overrides: tuple[str, ...],
    middleware_refs: tuple[str, ...],
    controls: tuple[str, ...],
    profile: str | None,
) -> Report:
    from mas.ctl.infra.bind import bind_session
    from mas.ctl.infra.show import document_agent_ids
    from mas.ctl.session.flavour import flavour_argument, resolve_session_flavour
    from mas.ctl.session.infra_resolve import resolve_session_infra
    from mas.ctl.session.manifest_config import spec_with_control_lists
    from mas.ctl.workspace.config import UserConfig, WorkspaceConfig

    path = Path(manifest).expanduser().resolve()
    loaded = safe_yaml_load(path.read_text(encoding="utf-8"), source=manifest)
    if not isinstance(loaded, dict):
        raise click.ClickException(f"{manifest}: expected a mapping")

    try:
        loaded = loaded_session_mas(loaded) or loaded
    except AgencyGroupError as exc:
        raise click.ClickException(str(exc)) from exc
    start = path.parent
    flavour_spec = resolve_session_flavour(
        flavour_argument(flavour), overrides=overrides, controls=controls, search_dirs=[start]
    )
    workspace = WorkspaceConfig.load(start).with_cli_overrides(overrides)
    infra = resolve_session_infra(
        loaded,
        workspace,
        UserConfig.load(),
        infra_refs_cli=infra_refs,
        overrides=overrides,
        runtime_refs_cli=runtime_refs,
        middleware_refs=middleware_refs,
        anchor=start,
        with_interceptors=True,
    )
    ids = document_agent_ids(loaded)
    groups = group_members(loaded) if loaded else None
    infra = bind_session(infra, flavour_spec, agent_ids=ids, groups=groups)
    posture, waivers = posture_of(flavour_spec)
    agents = {
        agent_id: spec_with_control_lists(spec, flavour_spec, agent_id=agent_id, groups=groups)
        for agent_id, spec in _agent_specs(loaded, path).items()
    }
    return evaluate(from_session(agents, infra), profile or posture, waivers)


def _render(report: Report) -> str:
    lines = [f"posture: {report.posture}"]
    for result in report.results:
        mark = {"passed": "ok  ", "failed": "FAIL", "waived": "WAIV", "deferred": "run "}[result.outcome]
        lines.append(f"{mark} {result.check} [{result.severity}]")
        for problem in result.problems:
            lines.append(f"       {problem}")
        if result.outcome == "failed":
            lines.append(f"       fix: {result.fix}")
        if result.reason:
            lines.append(f"       waiver: {result.reason}")
    lines.append(f"{len(report.failed)} failed, {len(report.waived)} waived")
    return "\n".join(lines)


@hardening_group.command("check", epilog=CHECK_EPILOG)
@click.argument("manifest", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--profile",
    type=click.Choice([DEFAULT, PRODUCTION]),
    default=None,
    help="Posture to judge by. Omitted, the Flavour's spec.hardening.profile (else default: advisory).",
)
@click.option("--flavour", default=None, help="Flavour name or path (omitted: local).")
@click.option("--infra-ref", "infra_refs", multiple=True, help="Infra bundle ref. Repeatable.")
@click.option("--runtime-ref", "runtime_refs", multiple=True, help="RuntimeEngine ref. Repeatable.")
@click.option("--override", "overrides", multiple=True, help="Schema-backed PATH=VALUE patch.")
@middleware_option
@control_option
@click.option("--json", "as_json", is_flag=True, help="Print the report as JSON.")
def hardening_check(
    manifest: str,
    profile: str | None,
    flavour: str | None,
    infra_refs: tuple[str, ...],
    runtime_refs: tuple[str, ...],
    overrides: tuple[str, ...],
    middleware_refs: tuple[str, ...],
    controls: tuple[str, ...],
    as_json: bool,
) -> None:
    """Run the hardening checks over MANIFEST (an Agent or MAS) as a session would resolve it."""
    from mas.ctl.infra.resolve import InfraResolveError
    from mas.library.standard.lib.stack import StackError

    try:
        report = _session_report(
            manifest,
            flavour=flavour,
            infra_refs=infra_refs,
            runtime_refs=runtime_refs,
            overrides=overrides,
            middleware_refs=middleware_refs,
            controls=controls,
            profile=profile,
        )
    except (ValueError, OSError, InfraResolveError, StackError, HardeningError) as exc:
        raise click.ClickException(str(exc)) from None
    except yaml.YAMLError as exc:
        raise click.ClickException(yaml_error_text(exc)) from None
    click.echo(json.dumps(report.as_dict(), indent=2) if as_json else _render(report))
    if report.posture == PRODUCTION and not report.ok:
        raise SystemExit(1)


__all__ = ["BY_ID", "hardening_group"]
