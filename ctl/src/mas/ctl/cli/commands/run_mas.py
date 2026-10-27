#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""mas-ctl run-mas — execute a MAS manifest locally."""

from __future__ import annotations

import click
import yaml
from mas.ctl.cli.bind_flags import bind_option, connect_option, with_connects
from mas.ctl.cli.obs_flags import observability_options, resolve_observability_config
from mas.ctl.cli.runtime_flags import runtime_id_choice
from mas.ctl.cli.trace_flags import mas_ctl_from_configs, resolve_trace_settings, trace_options
from mas.ctl.deployment.runtime_id import DEFAULT_RUNTIME_ID
from mas.ctl.executor.run_mas import execute_run_mas


@click.command("run-mas")
@click.argument("manifest", required=False)
@click.option("-p", "--prompt", default=None)
@click.option("-q", "--query", "queries", multiple=True, help="Single or multi-turn query")
@click.option("-o", "--overlay", "overlays", multiple=True, type=click.Path())
@click.option(
    "--override",
    "overrides",
    multiple=True,
    metavar="PATH=VALUE",
    help="Schema-validated overlay override (repeatable; applied last).",
)
@connect_option
@bind_option
@click.option("-d", "--deployment", "deployment", default=None, type=click.Path())
@click.option(
    "--flavour",
    default="local",
    show_default=True,
    help="Deployment flavour from library-standard (only 'local' supported for now)",
)
@click.option("--infra-ref", "infra_refs", multiple=True)
@click.option(
    "--kernel",
    default=DEFAULT_RUNTIME_ID,
    type=runtime_id_choice(),
)
@click.option("-i", "--interactive", is_flag=True)
@click.option(
    "--auto-hitl/--no-auto-hitl",
    default=True,
    help="Auto-resolve HITL in batch mode (default). Use --no-auto-hitl for OperatorConsole prompts.",
)
@click.option("--single-turn", is_flag=True)
@click.option("--no-validate", is_flag=True)
@observability_options
@trace_options
@click.pass_context
def run_mas_cmd(
    ctx: click.Context,
    manifest: str | None,
    prompt: str | None,
    queries: tuple[str, ...],
    overlays: tuple[str, ...],
    overrides: tuple[str, ...],
    connects: tuple[str, ...],
    binds: tuple[str, ...],
    deployment: str | None,
    flavour: str,
    infra_refs: tuple[str, ...],
    kernel: str,
    interactive: bool,
    auto_hitl: bool,
    single_turn: bool,
    no_validate: bool,
    events,
    events_file,
    events_stdout,
    events_format,
    trace_mode: str | None,
    no_trace: bool,
    trace_timestamps: bool | None,
    trace_engine: bool,
    trace_summary: bool,
    trace_full: bool,
    trace_color: bool | None,
) -> None:
    """Run a MAS manifest (compose → materialize → session on entry agent)."""
    from mas.ctl.paths import manifest_cwd, resolve_overlay_path

    if manifest is None:
        manifest = "mas.yaml"
    verbose = int(ctx.obj.get("verbose", 0) if ctx.obj else 0)
    overrides = with_connects(connects=connects, binds=binds, overrides=overrides)

    from mas.ctl.session.flavour import FlavourError, resolve_flavour

    try:
        flavour_spec = resolve_flavour(flavour, overrides=overrides)
    except FlavourError as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(2) from None

    with manifest_cwd(manifest, overlay_paths=overlays) as session:
        from mas.ctl.workspace.config import UserConfig, WorkspaceConfig

        workspace = WorkspaceConfig.load(session.manifest_dir or session.original_cwd)
        workspace = workspace.with_cli_overrides(overrides)
        user = UserConfig.load()
        trace = resolve_trace_settings(
            trace_mode=trace_mode,
            no_trace=no_trace,
            trace_summary=trace_summary,
            trace_full=trace_full,
            trace_timestamps=trace_timestamps,
            trace_engine=trace_engine,
            trace_color=trace_color,
            mas_ctl=mas_ctl_from_configs(user.mas_ctl, workspace.mas_ctl),
            verbose=verbose,
        )
        deployment_path = None
        deployment_doc = None
        if deployment:
            deployment_path = resolve_overlay_path(
                deployment,
                orig_cwd=session.original_cwd,
                manifest_dir=session.manifest_dir,
            )
            deployment_doc = yaml.safe_load(deployment_path.read_text(encoding="utf-8"))

        mas_doc = yaml.safe_load(session.local_manifest.read_text(encoding="utf-8"))
        obs_cfg = resolve_observability_config(
            events=events,
            events_file=events_file,
            events_stdout=events_stdout,
            events_format=events_format,
            manifest=mas_doc,
            deployment=deployment_doc,
            flavour_spec=flavour_spec,
        )
        rc = execute_run_mas(
            session.local_manifest,
            prompt=prompt,
            queries=list(queries) if queries else None,
            overlay_paths=list(session.overlays),
            overrides=list(overrides),
            infra_refs=list(infra_refs),
            deployment_path=deployment_path,
            kernel_backend=kernel,
            single_turn=single_turn,
            interactive=interactive,
            auto_hitl=auto_hitl,
            validate=not no_validate,
            verbose=verbose,
            manifest_dir=session.manifest_dir,
            obs_config=obs_cfg,
            **trace.as_session_kwargs(),
        )
    raise SystemExit(rc)
