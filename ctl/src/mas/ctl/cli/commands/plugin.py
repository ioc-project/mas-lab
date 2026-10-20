#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""mas-ctl plugin — list, enable, and diagnose runtime plugin availability."""

from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path

import click
import yaml


def _registry():
    from mas.runtime.registry import get_registry

    return get_registry()


def _install_hint(urn: str, missing: list[str], extra: str) -> str:
    return f"mas-ctl plugin enable {urn}" if extra else f"pip install {' '.join(missing)}"


@click.group("plugin")
def plugin_group() -> None:
    """Inspect and manage runtime plugin availability (library.yaml plugins:)."""


@plugin_group.command("list")
@click.option("--type", "plugin_type", default=None, help="Filter by plugin type/category.")
@click.option("--json", "as_json", is_flag=True)
def list_cmd(plugin_type: str | None, as_json: bool) -> None:
    """List discovered plugins with their availability (``available``/``disabled``)."""
    reg = _registry()
    items = reg.list(plugin_type)
    if as_json:
        click.echo(json.dumps(items, indent=2))
        return
    if not items:
        scope = f" for type {plugin_type!r}" if plugin_type else ""
        click.echo(f"No plugins registered{scope}.")
        return
    current_category = ""
    for item in sorted(items, key=lambda i: (i["category"], i["urn"])):
        if item["category"] != current_category:
            current_category = item["category"]
            click.echo(f"\n[{current_category}]")
        if item["available"]:
            click.echo(f"  {item['urn']:<40} available")
            continue
        hint = _install_hint(item["urn"], item["missing"], item["extra"])
        click.echo(
            f"  {item['urn']:<40} disabled  (missing: {', '.join(item['missing'])}; {hint})"
        )


@plugin_group.command("enable")
@click.argument("urn")
@click.option("--dry-run", is_flag=True, help="Print the install command without running it.")
def enable_cmd(urn: str, dry_run: bool) -> None:
    """Install the extra/deps an unavailable plugin needs, then re-check."""
    reg = _registry()
    entry = reg.get_entry(urn)
    if entry is None:
        raise click.ClickException(f"unknown plugin URN or alias: {urn!r}")
    info = entry.default
    if info is None:
        raise click.ClickException(f"plugin {entry.urn!r} has no default variant")

    missing = info.missing_requires()
    if not missing:
        click.echo(f"{entry.urn} is already available.")
        return

    target = info.extra or " ".join(missing)
    cmd = ["uv", "pip", "install", target]
    click.echo(f"Installing: {' '.join(cmd)}")
    if dry_run:
        return

    subprocess.run(cmd, check=True)
    importlib.invalidate_caches()
    still_missing = info.missing_requires()
    if still_missing:
        raise click.ClickException(
            f"{entry.urn} still missing after install: {', '.join(still_missing)}"
        )
    click.echo(f"{entry.urn} is now available.")


@plugin_group.command("doctor")
@click.argument("manifests", nargs=-1, type=click.Path(exists=True, path_type=Path))
@click.option("--json", "as_json", is_flag=True)
def doctor_cmd(manifests: tuple[Path, ...], as_json: bool) -> None:
    """Check plugin availability for the given spec/experiment files.

    With no arguments, checks every plugin currently registered — a
    whole-environment report instead of one spec's references.
    """
    reg = _registry()
    if manifests:
        entries, unresolved = _entries_referenced_by(reg, manifests)
    else:
        entries, unresolved = reg.all_entries(), []

    rows = []
    for entry in entries:
        info = entry.default
        missing = info.missing_requires() if info else ["<no default variant>"]
        rows.append(
            {
                "urn": entry.urn,
                "available": not missing,
                "missing": missing,
                "extra": info.extra if info else "",
            }
        )
    for urn in unresolved:
        rows.append({"urn": urn, "available": False, "missing": ["<unknown plugin URN>"], "extra": ""})

    if as_json:
        click.echo(json.dumps(rows, indent=2))
    else:
        for row in rows:
            if row["available"]:
                click.echo(f"  ok       {row['urn']}")
            else:
                hint = _install_hint(row["urn"], row["missing"], row["extra"])
                click.echo(f"  MISSING  {row['urn']}  ({', '.join(row['missing'])}; {hint})")

    if any(not row["available"] for row in rows):
        raise click.ClickException("one or more plugins are unavailable")


def _entries_referenced_by(reg, manifests: tuple[Path, ...]):
    """Plugin entries bound under a ``spec.<key>`` slot in any of *manifests*."""
    urns: set[str] = set()
    for path in manifests:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        spec = data.get("spec") or {}
        for spec_key in reg.runtime_spec_keys():
            binding = spec.get(spec_key)
            if binding is None:
                continue
            name = reg.binding_plugin_id(binding) if isinstance(binding, dict) else str(binding)
            if not name:
                continue
            urns.add(reg.urn_for(name) or name)

    entries = []
    unresolved = []
    for urn in sorted(urns):
        entry = reg.get_entry(urn)
        if entry is None:
            unresolved.append(urn)
        else:
            entries.append(entry)
    return entries, unresolved
