#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""mas-ctl serve — dedicated process for one agent_expose endpoint."""

from __future__ import annotations

from pathlib import Path

import click
from mas.ctl.cli.bind_flags import bind_option, connect_option, listen_option, with_connects
from mas.ctl.cli.obs_flags import resolve_observability_config
from mas.ctl.env import load_dotenv
from mas.ctl.infra.resolve import application_endpoint_is_deployed
from mas.ctl.runtime_cli import load_merged_agent_manifest
from mas.ctl.session.bootstrap import InstantiationOptions, instantiate_runtime
from mas.ctl.session.exposure import expose_agent, make_runtime_handler
from mas.ctl.session.flavour import FlavourError, resolve_flavour
from mas.ctl.session.infra_resolve import resolve_session_infra
from mas.ctl.session.manifest_config import require_agent_id_from_manifest
from mas.ctl.session.observability import setup_observability
from mas.ctl.workspace.config import UserConfig, WorkspaceConfig

# Re-export for tests that imported the handler from this command module.
_make_runtime_handler = make_runtime_handler


@click.command(name="serve")
@click.argument("agent", type=click.Path(exists=True, dir_okay=False, path_type=Path))
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
@listen_option
@click.option(
    "--infra-ref",
    "infra_refs_cli",
    multiple=True,
    help="Infrastructure manifest/bundle containing the agent's exposed Application endpoint",
)
@click.option(
    "--cache-read-only",
    is_flag=True,
    help="Answer only from the recorded answers: never call the LLM, never write the cache, "
    "fail at once on an unrecorded prompt. Needs no API key.",
)
@click.option("--flavour", default="local", show_default=True)
@click.option(
    "--control-dir",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Advertise ControlContract so mas-ctl control can attach by A2A contextId "
    "(default: $XDG_RUNTIME_DIR/mas-ctl, else /var/run/mas-ctl, else temp)",
)
@click.option("--no-control", is_flag=True, help="Do not advertise a control endpoint")
def serve_agent_cmd(
    agent: Path,
    overlays: tuple[str, ...],
    overrides: tuple[str, ...],
    connects: tuple[str, ...],
    binds: tuple[str, ...],
    listens: tuple[str, ...],
    infra_refs_cli: tuple[str, ...],
    cache_read_only: bool,
    flavour: str,
    control_dir: Path | None,
    no_control: bool,
) -> None:
    """Serve an agent through the agent_expose plugin selected by its infra endpoint."""
    from mas.ctl.paths import manifest_cwd

    overrides = with_connects(
        connects=connects, binds=binds, overrides=overrides, listens=listens
    )

    with manifest_cwd(agent, overlay_paths=overlays) as session:
        load_dotenv(cwd=session.original_cwd, manifest_dir=session.manifest_dir)
        workspace = WorkspaceConfig.load(session.manifest_dir or session.original_cwd)
        workspace = workspace.with_cli_overrides(overrides)
        cache_read_only = cache_read_only or workspace.cache_read_only
        user = UserConfig.load()
        manifest, pattern_plugin_id = load_merged_agent_manifest(
            session.local_manifest,
            manifest_dir=session.manifest_dir,
            overlays=tuple(str(path) for path in session.overlays),
            overrides=overrides,
        )
        if manifest is None:
            raise click.ClickException("serve requires an agent manifest")

        try:
            flavour_spec = resolve_flavour(flavour, overrides=overrides)
        except FlavourError as error:
            raise click.BadParameter(str(error), param_hint="--flavour") from error

        infra = resolve_session_infra(
            manifest,
            workspace,
            user,
            infra_refs_cli=infra_refs_cli,
            overrides=overrides,
            runtime_refs_cli=(),
            anchor=session.manifest_dir or session.original_cwd,
            with_interceptors=True,
        )
        agent_id = require_agent_id_from_manifest(manifest)
        endpoint = infra.applications.get(agent_id)
        if endpoint is None or not application_endpoint_is_deployed(endpoint):
            raise click.ClickException(
                f"No deployed infra Application endpoint named {agent_id!r}; set usage: deploy "
                "or usage: use-and-deploy "
                "in workspace infra_refs or pass --infra-ref"
            )

        instance, _ = instantiate_runtime(
            InstantiationOptions(
                pattern_plugin_id=pattern_plugin_id,
                agent_manifest=manifest,
                manifest_dir=session.manifest_dir,
                resolved_infra=infra,
                workspace=workspace,
                cache_read_only=cache_read_only,
            )
        )
        obs_config = resolve_observability_config(
            events=None,
            events_file=None,
            events_stdout=False,
            events_format=None,
            agent_id=agent_id,
            manifest=manifest,
            flavour_spec=flavour_spec,
        )
        recorder = setup_observability(
            instance,
            obs_config,
            base_dir=session.manifest_dir or session.original_cwd,
        )
        from mas.ctl.session.control_dir import default_control_dir

        try:
            expose_agent(
                instance,
                manifest,
                endpoint,
                blocking=True,
                control_dir=None if no_control else (control_dir or default_control_dir()),
            )
        finally:
            if recorder is not None:
                recorder.close()
