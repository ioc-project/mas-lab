#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab validate`` — validate experiment, pipeline, and lab-config manifests."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import click
import yaml


@click.command("validate")
@click.argument("manifests", nargs=-1, required=True, metavar="MANIFEST...")
@click.option(
    "--kind", "-k",
    type=click.Choice(["experiment", "pipeline", "lab-config"], case_sensitive=False),
    default=None,
    help="Manifest kind (auto-detected from top-level key when omitted).",
)
@click.option(
    "--base-dir", "-b",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Base directory for resolving relative-path references (defaults to the manifest's own directory).",
)
@click.option(
    "--strict/--no-strict",
    default=True,
    show_default=True,
    help="Error on violations (--strict, default) or warn only (--no-strict).",
)
@click.option(
    "--resolve-refs/--no-resolve-refs",
    default=True,
    show_default=True,
    help="Check that referenced files exist on disk (--resolve-refs, default). Use --no-resolve-refs for template manifests or CI without a full repo checkout.",  # noqa: E501
)
def validate_cmd(
    manifests: tuple[str, ...],
    kind: Optional[str],
    base_dir: Optional[Path],
    strict: bool,
    resolve_refs: bool,
) -> None:
    """Validate one or more mas-lab manifest YAML files.

    Accepted manifest types:

    \b
      experiment.yaml   — batch benchmark configuration  (top-level key: experiment:)
      pipeline.yaml     — post-processing pipeline        (top-level key: pipeline:)
      lab-config.yaml   — interactive demo configuration  (top-level key: lab:)

    Validation checks:

    \b
      1. JSON Schema (Draft-07) — unknown fields, wrong types, missing required keys.
         Removed experiment keys (pipeline_bind, pipeline, plots, flavours,
         output_dir, mas) fail validation.
      2. Deprecated keys — still valid so old files load, printed as warnings
         (applications:, test:, application used as a pipeline level).
      3. Reference availability — mas.manifest, dataset.path, pipeline step paths,
                                   etc. must exist on disk (disable with --no-resolve-refs).

    Examples:

    \b
      mas-lab validate experiment.yaml
      mas-lab validate pipeline.yaml --no-resolve-refs
      mas-lab validate lab-config.yaml --kind lab-config --no-strict
      mas-lab validate experiments/*/experiment.yaml
    """
    from mas.lab.manifests.validator import (
        ManifestValidationError,
        detect_kind,
        validate_manifest,
    )

    failed = 0

    for manifest_path_str in manifests:
        manifest_path = Path(manifest_path_str).resolve()

        if not manifest_path.exists():
            click.echo(f"FAIL   {manifest_path_str}  [not found]", err=True)
            failed += 1
            continue

        try:
            with manifest_path.open(encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except Exception as exc:
            click.echo(f"FAIL   {manifest_path_str}  [YAML parse error: {exc}]", err=True)
            failed += 1
            continue

        # Kind detection
        effective_kind = kind
        if effective_kind is None:
            effective_kind = detect_kind(data)
        if effective_kind is None:
            click.echo(
                f"FAIL   {manifest_path_str}  [cannot detect kind — expected top-level key 'experiment:', 'pipeline:', or 'lab:']",  # noqa: E501
                err=True,
            )
            failed += 1
            continue

        # Base directory
        effective_base_dir = base_dir or manifest_path.parent

        try:
            deprecations = validate_manifest(
                data,
                source=str(manifest_path),
                kind=effective_kind,
                strict=strict,
                base_dir=effective_base_dir,
                resolve_refs=resolve_refs,
            ) or []
            if deprecations:
                click.echo(
                    f"OK     {manifest_path_str}  [{effective_kind}]  "
                    f"({len(deprecations)} deprecated)"
                )
                for warning in deprecations:
                    click.echo(f"       deprecated: {warning}")
            else:
                click.echo(f"OK     {manifest_path_str}  [{effective_kind}]")
        except ManifestValidationError as exc:
            click.echo(f"FAIL   {manifest_path_str}  [{effective_kind}]", err=True)
            for violation in exc.violations:
                click.echo(f"       {violation}", err=True)
            failed += 1

    sys.exit(1 if failed else 0)
