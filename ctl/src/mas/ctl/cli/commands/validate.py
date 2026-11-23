#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""mas-ctl validate — JSON Schema validation for all manifest kinds."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click
from mas.ctl.validate import validate_file, validate_tree, validation_enabled
from mas.runtime.spec.yaml_errors import YamlLoadError, safe_yaml_load


@click.command("validate")
@click.argument("paths", nargs=-1, type=click.Path(exists=True))
@click.option("--kind", "-k", default=None, help="Force manifest kind")
@click.option(
    "--strict/--no-strict",
    default=True,
    help="Treat schema violations as errors (default strict)",
)
@click.option("--no-validate", is_flag=True, help="Skip validation (print ok and exit 0)")
@click.option(
    "--resolve-refs/--no-resolve-refs",
    default=True,
    help="Check referenced files exist on disk (default on)",
)
@click.option("-o", "--overlay", "overlays", multiple=True, type=click.STRING)
@click.option(
    "--profile",
    type=click.Choice(["default", "production"]),
    default=None,
    help="Also run the named hardening checks over the files. production fails on one; default only prints.",
)
def validate_cmd(
    paths: tuple[str, ...],
    kind: str | None,
    strict: bool,
    no_validate: bool,
    resolve_refs: bool,
    overlays: tuple[str, ...],
    profile: str | None,
) -> None:
    """Validate YAML manifests against docs/schemas (v1 + v2 kinds)."""
    if no_validate or not validation_enabled():
        click.echo("validation skipped")
        return

    if not paths:
        click.echo("usage: mas-ctl validate <file-or-dir>...", err=True)
        raise SystemExit(1)

    from mas.ctl.overlay import merge_overlay
    from mas.ctl.overlay.normalize import normalize_overlay
    from mas.ctl.session.flavour_control import is_control_document
    from mas.ctl.validate import validate_data

    failed = 0
    for raw in paths:
        path = Path(raw)
        if path.is_file():
            if overlays and path.suffix in (".yaml", ".yml"):
                data = _load(path)
                # The manifest kind comes from the base document, not the overlay target.
                from mas.ctl.validate.schemas import declared_kind

                base_kind = kind or declared_kind(data) or "agent"
                from mas.ctl.paths import resolve_overlay_path

                for ov in overlays:
                    ov_path = resolve_overlay_path(ov, orig_cwd=Path.cwd(), manifest_dir=path.parent)
                    ov_raw = _load(ov_path)
                    if is_control_document(ov_raw):
                        ov_result = validate_data(
                            ov_raw,
                            source=str(ov_path),
                            kind="control",
                            strict=strict,
                            base_dir=ov_path.parent,
                            resolve_refs=resolve_refs,
                        )
                        _print_result(ov_result)
                        if not ov_result.ok:
                            failed += 1
                        continue
                    normalized = normalize_overlay(ov_raw, name=ov_path.stem)
                    ov_result = validate_data(
                        normalized,
                        source=str(ov_path),
                        kind="overlay",
                        strict=strict,
                        base_dir=ov_path.parent,
                        resolve_refs=resolve_refs,
                    )
                    _print_result(ov_result)
                    if not ov_result.ok:
                        failed += 1
                        continue
                    data = merge_overlay(data, normalized)
                result = validate_data(
                    data,
                    source=str(path),
                    kind=base_kind,
                    strict=strict,
                    base_dir=path.parent,
                    resolve_refs=resolve_refs,
                )
            else:
                result = validate_file(path, kind=kind, strict=strict, resolve_refs=resolve_refs)
            _print_result(result)
            if not result.ok:
                failed += 1
            continue
        for result in validate_tree(path, strict=strict, resolve_refs=resolve_refs):
            _print_result(result)
            if not result.ok:
                failed += 1

    if profile and _hardening_failed(paths, profile):
        failed += 1
    raise SystemExit(1 if failed else 0)


def _hardening_failed(paths: tuple[str, ...], profile: str) -> bool:
    """Run the hardening checks over the files read alone; True when ``production`` has a failed check."""
    from mas.ctl.hardening import evaluate
    from mas.ctl.hardening.collect import from_documents

    documents: list[tuple[str, dict]] = []
    for raw in paths:
        path = Path(raw)
        files = [path] if path.is_file() else sorted([*path.rglob("*.yaml"), *path.rglob("*.yml")])
        for file in files:
            try:
                loaded = safe_yaml_load(file.read_text(encoding="utf-8"))
            except (OSError, YamlLoadError):
                continue
            if isinstance(loaded, dict):
                documents.append((str(file), loaded))
    report = evaluate(from_documents(documents), profile)
    click.echo(f"hardening  profile={profile}  failed={len(report.failed)}")
    for result in report.failed:
        for problem in result.problems:
            click.echo(f"  [{'error' if profile == 'production' else 'warning'}] {result.check}: {problem}")
        click.echo(f"      fix: {result.fix}")
    return profile == "production" and not report.ok


def _load(path: Path) -> Any:
    try:
        return safe_yaml_load(path.read_text(encoding="utf-8"), source=str(path))
    except YamlLoadError as exc:
        raise click.ClickException(str(exc)) from None


def _print_result(result: Any) -> None:
    status = "OK" if result.ok else "FAIL"
    click.echo(f"{status}  {result.source}  kind={result.kind}")
    for issue in result.issues:
        loc = f" @ {issue.path}" if issue.path else ""
        click.echo(f"  [{issue.level}]{loc} {issue.message}")


@click.command("schemas")
def schemas_cmd() -> None:
    """List registered schema kinds."""
    from mas.ctl.validate.schemas import list_schema_kinds, schema_root

    click.echo(f"schema root: {schema_root()}")
    for k in list_schema_kinds():
        click.echo(f"  {k}")
