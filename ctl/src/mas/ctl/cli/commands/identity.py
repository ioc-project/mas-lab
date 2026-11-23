#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""mas-ctl identity: look at the identities a session would use. Never prints a secret."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click
from mas.ctl.cli.bind_flags import control_option, middleware_option
from mas.runtime.secrets.manifest import load_checked_document

EXIT_PROBLEMS = 1


def _agent_specs(document: dict[str, Any] | None, base: Path) -> dict[str, dict[str, Any]]:
    """Each agent's own spec: a MAS document's ``agency.agents[].ref`` files, else the Agent document itself."""
    if not isinstance(document, dict):
        return {}
    spec = document.get("spec") if isinstance(document.get("spec"), dict) else {}
    if str(document.get("kind") or "") != "MAS":
        name = str((document.get("metadata") or {}).get("name") or "")
        return {name: dict(spec)} if name else {}
    out: dict[str, dict[str, Any]] = {}
    agency = spec.get("agency") if isinstance(spec.get("agency"), dict) else {}
    for entry in agency.get("agents") or []:
        if not isinstance(entry, dict) or not entry.get("id") or not entry.get("ref"):
            continue  # an inline agent has no document: it is judged on its Controls alone
        try:
            loaded = load_checked_document(base / str(entry["ref"]))
        except (OSError, ValueError):
            continue
        if isinstance(loaded, dict) and isinstance(loaded.get("spec"), dict):
            out[str(entry["id"])] = dict(loaded["spec"])
    return out


def _session(manifest, infra_refs, overrides, controls, flavour, middleware_refs):
    """The bound infra, the Flavour, each agent's own spec and the agent ids, resolved as ``infra resolved`` does."""
    from mas.ctl.infra.bind import bind_flavour, bind_session
    from mas.ctl.infra.resolve import InfraResolveError
    from mas.ctl.infra.show import document_agent_ids
    from mas.ctl.session.flavour import flavour_argument, resolve_session_flavour
    from mas.ctl.session.infra_resolve import resolve_session_infra
    from mas.ctl.workspace.config import UserConfig, WorkspaceConfig
    from mas.library.standard.lib.stack import StackError

    start = Path(manifest).expanduser().resolve().parent if manifest else Path.cwd()
    workspace = WorkspaceConfig.load(start).with_cli_overrides(overrides)
    document = None
    if manifest:
        try:
            document = load_checked_document(Path(manifest).expanduser())
        except (OSError, ValueError) as exc:
            raise click.ClickException(str(exc)) from exc
    try:
        flavour_spec = resolve_session_flavour(
            flavour_argument(flavour), overrides=overrides, controls=controls, search_dirs=[start]
        )
        infra = resolve_session_infra(
            document,
            workspace,
            UserConfig.load(),
            infra_refs_cli=infra_refs,
            overrides=overrides,
            middleware_refs=middleware_refs,
            anchor=start,
            with_interceptors=True,
        )
        ids = document_agent_ids(document) if document else []
        infra = (
            bind_session(infra, flavour_spec, agent_ids=ids)
            if document
            else bind_flavour(infra, flavour_spec, agent_ids=None)
        )
    except (ValueError, InfraResolveError, StackError) as exc:
        raise click.ClickException(str(exc)) from exc
    return infra, flavour_spec, _agent_specs(document, start), ids


def _options(fn: Any) -> Any:
    for decorator in reversed(
        [
            click.argument("manifest", required=False),
            click.option("--infra-ref", "infra_refs", multiple=True, help="Infrastructure ref (repeatable)."),
            click.option(
                "--override", "overrides", multiple=True, metavar="PATH=VALUE", help="Schema-checked override."
            ),
            middleware_option,
            control_option,
            click.option("--flavour", default=None, help="Flavour: bundled name or a Flavour YAML path."),
            click.option("--json", "as_json", is_flag=True, help="Print the stable JSON document."),
        ]
    ):
        fn = decorator(fn)
    return fn


def _emit(payload: dict[str, Any], as_json: bool, lines: list[str]) -> None:
    click.echo(json.dumps(payload, indent=2, default=str) if as_json else "\n".join(lines))


@click.group("identity")
def identity_group() -> None:
    """The identities a session would use: check them, list them, describe one."""


@identity_group.command("check")
@_options
@click.option(
    "--require-gate", is_flag=True, help="A holder without an identity-gate in its governance list is an error."
)
def identity_check(manifest, infra_refs, overrides, middleware_refs, controls, flavour, as_json, require_gate) -> None:
    """Preflight: records resolve, schemes are installed, params are valid, secrets are set, gate requirements fit.

    Exit 0 when nothing is wrong, 1 with each problem and how to fix it. A secret is never printed.
    """
    from mas.ctl.infra.identity_check import check

    infra, flavour_spec, agents, ids = _session(manifest, infra_refs, overrides, controls, flavour, middleware_refs)
    report = check(infra, flavour_spec, agents, ids, require_gate=require_gate)
    lines = [("OK" if report["ok"] else "FAILED")] + [f"  problem: {p}" for p in report["problems"]]
    lines += [f"  warning: {w}" for w in report["warnings"]]
    _emit(report, as_json, lines)
    if not report["ok"]:
        raise SystemExit(EXIT_PROBLEMS)


@identity_group.command("list")
@_options
def identity_list(manifest, infra_refs, overrides, middleware_refs, controls, flavour, as_json) -> None:
    """List the Identity records, their scheme and who uses them (by agent). Nothing secret is printed."""
    from mas.ctl.infra.identity_check import listing

    infra, _flavour, _agents, ids = _session(manifest, infra_refs, overrides, controls, flavour, middleware_refs)
    payload = listing(infra, ids)
    lines = [
        f"{row['id']}  scheme={row['scheme']}  used by: "
        + (", ".join(sorted({u["agent"] for u in row["used_by"]})) or "(nobody)")
        for row in payload["identities"]
    ] or ["(no Identity rows)"]
    lines += [f"agent {agent}: {', '.join(records)}" for agent, records in payload["agents"].items()]
    _emit(payload, as_json, lines)


@identity_group.command("describe")
@click.argument("record")
@_options
def identity_describe(record, manifest, infra_refs, overrides, middleware_refs, controls, flavour, as_json) -> None:
    """Print the public view of each identity of RECORD (facts, attestation, advertise). Never a proof."""
    from mas.ctl.infra.identity_check import describe

    infra, *_ = _session(manifest, infra_refs, overrides, controls, flavour, middleware_refs)
    try:
        payload = describe(infra, record)
    except (LookupError, ValueError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(EXIT_PROBLEMS) from None
    except Exception as exc:  # a scheme's own failure: the type name only, never a message that may hold a value
        click.echo(f"error: describe of {record!r} failed ({type(exc).__name__})", err=True)
        raise SystemExit(EXIT_PROBLEMS) from None
    lines = [f"{payload['id']}  scheme={payload['scheme']}"] + [
        f"{item['identity']} ({item['status']}): {json.dumps(item['view'])}" for item in payload["identities"]
    ]
    _emit(payload, as_json, lines)


__all__ = ["identity_group"]
