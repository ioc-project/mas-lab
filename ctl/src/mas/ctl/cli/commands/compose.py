#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""mas-ctl compose — emit EffectiveBind + PlacementPlan."""

from __future__ import annotations

from pathlib import Path

import click
import yaml
from mas.ctl.cli.bind_flags import bind_option, with_binds
from mas.ctl.cli.runtime_flags import runtime_id_choice
from mas.ctl.compose.runner import ComposeRequest, compose_run
from mas.ctl.deployment.runtime_id import DEFAULT_RUNTIME_ID
from mas.ctl.paths import manifest_cwd, resolve_overlay_path


def _compose_run(
    manifest: str,
    *,
    deployment_path: str | None,
    overlay_paths: tuple[str, ...],
    overrides: tuple[str, ...] = (),
    infra_refs: tuple[str, ...],
    runtime_refs: tuple[str, ...],
    kernel_backend: str,
    validate: bool,
) -> dict:
    with manifest_cwd(manifest, overlay_paths=overlay_paths) as session:
        dep = None
        if deployment_path:
            dep = resolve_overlay_path(
                deployment_path,
                orig_cwd=session.original_cwd,
                manifest_dir=session.manifest_dir,
            )
        req = ComposeRequest(
            manifest=session.local_manifest,
            deployment_path=dep,
            overlay_paths=list(session.overlays),
            overrides=list(overrides),
            infra_refs=list(infra_refs),
            runtime_refs=list(runtime_refs),
            kernel_backend=kernel_backend,
            validate=validate,
        )
        result = compose_run(req)
        return {
            "mas_id": result.mas_id,
            "infra_refs": result.infra_refs,
            "effective_bind": result.effective_bind,
            "placement_plan": result.placement_plan,
            "deployment": result.deployment,
        }


@click.command("compose")
@click.argument("manifest")
@click.option("--deployment", "-d", "deployment_path", default=None, type=click.Path())
@click.option("--overlay", "-o", "overlay_paths", multiple=True, type=click.Path())
@click.option(
    "--override",
    "overrides",
    multiple=True,
    metavar="PATH=VALUE",
    help="Schema-validated overlay override (repeatable; applied last).",
)
@bind_option
@click.option("--infra-ref", "infra_refs", multiple=True)
@click.option("--runtime-ref", "runtime_refs", multiple=True)
@click.option(
    "--kernel",
    "kernel_backend",
    default=DEFAULT_RUNTIME_ID,
    type=runtime_id_choice(),
)
@click.option("--output", "-O", "output_path", default=None, type=click.Path())
@click.option("--no-validate", is_flag=True, help="Skip manifest validation")
def compose_cmd(
    manifest: str,
    deployment_path: str | None,
    overlay_paths: tuple[str, ...],
    overrides: tuple[str, ...],
    binds: tuple[str, ...],
    infra_refs: tuple[str, ...],
    runtime_refs: tuple[str, ...],
    kernel_backend: str,
    output_path: str | None,
    no_validate: bool,
) -> None:
    """Compose MAS + deployment into effective bind and placement plan."""
    payload = _compose_run(
        manifest,
        deployment_path=deployment_path,
        overlay_paths=overlay_paths,
        overrides=with_binds(binds, overrides),
        infra_refs=infra_refs,
        runtime_refs=runtime_refs,
        kernel_backend=kernel_backend,
        validate=not no_validate,
    )
    text = yaml.safe_dump(payload, sort_keys=False)
    if output_path:
        Path(output_path).write_text(text, encoding="utf-8")
        click.echo(f"Wrote {output_path}", err=True)
    else:
        click.echo(text)


@click.command("plan")
@click.argument("manifest")
@click.option("--deployment", "-d", "deployment_path", default=None, type=click.Path())
@click.option("--kernel", "kernel_backend", default=DEFAULT_RUNTIME_ID, type=runtime_id_choice())
@click.option("--no-validate", is_flag=True)
def plan_cmd(
    manifest: str,
    deployment_path: str | None,
    kernel_backend: str,
    no_validate: bool,
) -> None:
    """Emit placement plan only (compose subset)."""
    payload = _compose_run(
        manifest,
        deployment_path=deployment_path,
        overlay_paths=(),
        infra_refs=(),
        kernel_backend=kernel_backend,
        validate=not no_validate,
    )
    click.echo(yaml.safe_dump(payload["placement_plan"], sort_keys=False))
