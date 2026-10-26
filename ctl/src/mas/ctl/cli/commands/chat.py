#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""mas-ctl chat — stdout conversation UI (ctl owns all display)."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from mas.ctl.cli.bind_flags import bind_option, with_binds
from mas.ctl.cli.help_text import CHAT_EPILOG
from mas.ctl.cli.obs_flags import observability_options, resolve_observability_config
from mas.ctl.cli.trace_flags import mas_ctl_from_configs, resolve_trace_settings, trace_options
from mas.ctl.session.bootstrap import InstantiationOptions, instantiate_runtime
from mas.ctl.session.controller import (
    ConversationConfig,
    SessionController,
    close_observability,
    run_session_loop,
)
from mas.ctl.session.display_user_io_contract import ConversationDisplayUserIOContract
from mas.ctl.session.hitl_config import resolve_hitl_from_manifest
from mas.ctl.session.interactive_hitl_contract import InteractiveHitlContract
from mas.ctl.session.manager import SessionManager
from mas.ctl.session.manifest_config import require_agent_id_from_manifest
from mas.ctl.session.observability import setup_observability
from mas.ctl.session.protocol_hints import emit_session_protocol_hints
from mas.ctl.ui.stdout import StdoutConversationDisplay
from mas.runtime.session import ManifestRef
from mas.runtime.spec.checkpoint import parse_checkpoint_policy


@click.command("chat", epilog=CHAT_EPILOG)
@click.argument("manifest", required=False)
@click.option("--prompt", "-p", default=None)
@click.option("--query", "-q", "queries", multiple=True)
@click.option(
    "--interactive/--no-interactive",
    "-i/-I",
    default=None,
    help="Multi-turn REPL (default: TTY unless --single-turn)",
)
@click.option("--single-turn", is_flag=True, help="Exit after first agent reply")
@click.option("-o", "--overlay", "overlays", multiple=True, type=click.Path())
@click.option("--tool", "tools", multiple=True, help="Inline overlay: tool name")
@click.option("--skill", "skills", multiple=True, help="Inline overlay: skill name")
@click.option("--memory", default=None, help="Inline overlay: memory backend id")
@click.option("--set", "set_values", multiple=True, help="Inline overlay: spec.context KEY=VALUE")
@click.option(
    "--override",
    "overrides",
    multiple=True,
    metavar="PATH=VALUE",
    help="Schema-validated overlay override (repeatable; applied last).",
)
@bind_option
@click.option("--pattern", default=None, help="Design pattern plugin id (default from manifest)")
@click.option(
    "--flavour",
    default="local",
    show_default=True,
    help="Deployment flavour from library-standard (local, mcp exposure)",
)
@click.option(
    "--infra-ref",
    "infra_refs_cli",
    multiple=True,
    help="Infrastructure bundle ref (merged after workspace infra_refs)",
)
@click.option(
    "--runtime-ref",
    "runtime_refs_cli",
    multiple=True,
    help="RuntimeEngine manifest ref (merged after workspace runtime_refs)",
)
@click.option("--memory-seed", "memory_seed_path", default=None, type=click.Path())
@click.option("--checkpoint-dir", default=None, type=click.Path())
@click.option("--load-checkpoint", default=None, type=click.Path())
@click.option("--save-checkpoint/--no-save-checkpoint", default=False)
@click.option(
    "--control-dir",
    default=None,
    type=click.Path(file_okay=False),
    help="Advertise ControlContract so mas-ctl control can attach by session id "
    "(default: $XDG_RUNTIME_DIR/mas-ctl, else /var/run/mas-ctl, else temp)",
)
@click.option(
    "--no-control",
    is_flag=True,
    help="Do not advertise a control endpoint",
)
@click.option(
    "--debug-script",
    default=None,
    help="gdb-like script: @file or inline (same language as spec.debug).",
)
@click.option("--no-validate", is_flag=True, help="Skip schema validation for seeds/checkpoints")
@click.option(
    "--cache-read/--no-cache-read",
    default=None,
    help="Look up a cached response before calling the LLM (default: RuntimeEngine cache.read, else true)",
)
@click.option(
    "--cache-write/--no-cache-write",
    default=None,
    help="Persist a response to the cache after calling the LLM (default: RuntimeEngine cache.write, else true)",
)
@click.option(
    "--cache-read-only",
    is_flag=True,
    help="Answer only from the recorded answers: never call the LLM, never write the cache, "
    "fail at once on an unrecorded prompt. Needs no API key.",
)
@click.option(
    "--stream/--no-stream",
    default=None,
    help="Stream the LLM response over SSE instead of waiting for the full "
    "completion (default: RuntimeEngine stream / MAS_LLM_STREAM / false)",
)
@click.option(
    "--without-obs",
    is_flag=True,
    help="Disable envelope observability summand (M_obs) and event recording",
)
@click.option(
    "--without-gov",
    is_flag=True,
    help="Disable governance summand (M_gov), policy evaluation, and HITL chokepoints",
)
@observability_options
@trace_options
@click.option(
    "--model",
    default=None,
    help="Override spec.models for this run",
)
@click.option(
    "--max-tokens",
    type=click.IntRange(min=1),
    default=None,
    help="Alias of --override 'agent:spec.models[*].max_tokens=N' (applied before explicit --override)",
)
@click.pass_context
def chat_cmd(
    ctx: click.Context,
    manifest: str | None,
    prompt: str | None,
    queries: tuple[str, ...],
    interactive: bool | None,
    single_turn: bool,
    overlays: tuple[str, ...],
    tools: tuple[str, ...],
    skills: tuple[str, ...],
    memory: str | None,
    set_values: tuple[str, ...],
    overrides: tuple[str, ...],
    binds: tuple[str, ...],
    pattern: str | None,
    flavour: str,
    infra_refs_cli: tuple[str, ...],
    runtime_refs_cli: tuple[str, ...],
    memory_seed_path: str | None,
    checkpoint_dir: str | None,
    load_checkpoint: str | None,
    save_checkpoint: bool,
    control_dir: str | None,
    no_control: bool,
    debug_script: str | None,
    no_validate: bool,
    cache_read: bool | None,
    cache_write: bool | None,
    cache_read_only: bool,
    stream: bool | None,
    without_obs: bool,
    without_gov: bool,
    events: bool | None,
    events_file: str | None,
    events_stdout: bool,
    events_format: str | None,
    trace_mode: str | None,
    no_trace: bool,
    trace_timestamps: bool | None,
    trace_engine: bool,
    trace_summary: bool,
    trace_full: bool,
    trace_color: bool | None,
    model: str | None,
    max_tokens: int | None,
) -> None:
    """Run agent conversation on stdout (You:/Agent: labels).

    Use --help for session commands (/quit, /steer), HITL, and examples.
    """
    from mas.ctl.env import load_dotenv
    from mas.ctl.overrides import max_tokens_overrides
    from mas.ctl.runtime_cli import load_merged_agent_manifest
    from mas.ctl.session.infra_resolve import resolve_session_infra
    from mas.ctl.workspace.config import UserConfig, WorkspaceConfig

    if cache_read_only and (cache_write or cache_read is False):
        raise click.UsageError("--cache-read-only cannot be combined with --cache-write or --no-cache-read")
    overrides = with_binds(binds, (*max_tokens_overrides(max_tokens), *overrides))
    verbose = int(ctx.obj.get("verbose", 0) if ctx.obj else 0)

    hitl_responder, hitl_terminal = None, None

    from mas.ctl.paths import manifest_cwd, resolve_overlay_path, resolve_path

    with manifest_cwd(manifest, overlay_paths=overlays) as session:
        load_dotenv(cwd=session.original_cwd, manifest_dir=session.manifest_dir)
        workspace = WorkspaceConfig.load(session.manifest_dir or session.original_cwd)
        workspace = workspace.with_cli_overrides(overrides)
        if workspace.cache_read_only and cache_write is not True and cache_read is not False:
            cache_read_only = True
        user = UserConfig.load()
        overlay_strs = tuple(str(p) for p in session.overlays)
        agent_data, plugin = load_merged_agent_manifest(
            session.local_manifest if manifest else None,
            overlays=overlay_strs,
            tools=tools,
            skills=skills,
            memory=memory,
            set_values=set_values,
            overrides=overrides,
            pattern=pattern,
            validate=not no_validate,
        )
        if debug_script:
            _apply_debug_script_option(
                agent_data,
                debug_script,
                manifest_dir=session.manifest_dir or session.original_cwd,
            )
        if not manifest and load_checkpoint:
            checkpoint_path = resolve_overlay_path(
                load_checkpoint,
                orig_cwd=session.original_cwd,
                manifest_dir=session.manifest_dir,
            )
            embedded_manifest = _embedded_checkpoint_manifest(checkpoint_path)
            if embedded_manifest is not None:
                agent_data = embedded_manifest
                from mas.runtime.spec.parser import parse_agent_spec

                plugin = parse_agent_spec(agent_data.get("spec") or {})[0].pattern_plugin_id

        from mas.ctl.session.flavour import FlavourError, resolve_flavour

        # Deployment flavour: resolve + validate (only `local` is supported).
        # Surviving deployment concerns (currently: observability plugin
        # selection) are folded in below — see docs/design/flavour-boundary.md.
        try:
            flavour_spec = resolve_flavour(flavour, overrides=overrides)
        except FlavourError as exc:
            click.echo(f"error: {exc}", err=True)
            raise SystemExit(2) from None

        scripted: list[str] = []
        if prompt:
            scripted.append(prompt)
        scripted.extend(queries)
        if not scripted and not sys.stdin.isatty():
            scripted = [sys.stdin.read().strip()]

        if interactive is None:
            interactive = sys.stdin.isatty() and not single_turn and not scripted
        if single_turn:
            interactive = False

        hitl_responder, hitl_terminal = resolve_hitl_from_manifest(
            agent_data,
            session_interactive=interactive,
        )

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

        # Built here (not later, where it used to live) so it can also back
        # user_io_contract below -- same instance is reused for the
        # SessionController further down.
        display = StdoutConversationDisplay(
            out=click.get_text_stream("stdout"),
            verbose=verbose,
            show_labels=not interactive,
            user_prompt_echoed=interactive,
            trace=trace.enabled,
        )

        # Agent-initiated HITL (request_human_input)/inform_user() otherwise
        # default to a registry-based contract nothing in an interactive CLI
        # session ever resolves or reads. Give interactive sessions a real
        # resolver/display instead; non-interactive runs keep the default
        # (registry) contracts -- unset here means RegistryHitlContract/
        # RegistryUserIOContract, matching batch/scripted behavior unchanged.
        hitl_contract = InteractiveHitlContract() if interactive else None
        user_io_contract = ConversationDisplayUserIOContract(display) if interactive else None

        def _opt_file(path: str | None) -> Path | None:
            if not path:
                return None
            return resolve_overlay_path(path, orig_cwd=session.original_cwd, manifest_dir=session.manifest_dir)

        def _opt_dir(path: str | None) -> Path | None:
            if not path:
                return None
            return resolve_path(
                path,
                orig_cwd=session.original_cwd,
                manifest_dir=session.manifest_dir,
                expect_dir=True,
                create_dir=True,
            )

        spec_data = (agent_data or {}).get("spec") or {}
        checkpoint_policy = parse_checkpoint_policy(spec_data.get("checkpoint"))
        resolved_checkpoint_dir = _opt_dir(checkpoint_dir)
        if resolved_checkpoint_dir is None and checkpoint_policy.storage_path:
            resolved_checkpoint_dir = _opt_dir(checkpoint_policy.storage_path)
        if resolved_checkpoint_dir is None and (
            checkpoint_policy.uses_disk() or checkpoint_policy.auto_resume_latest
        ):
            checkpoint_anchor = session.manifest_dir or session.original_cwd
            resolved_checkpoint_dir = checkpoint_anchor / ".mas" / "checkpoints"

        load_checkpoint_path = _opt_file(load_checkpoint)
        if checkpoint_policy.auto_resume_latest and load_checkpoint_path is None:
            from mas.ctl.adapters.checkpoint import JsonCheckpointStore

            expected_hash = ManifestRef.from_content(agent_data or {}).content_hash
            candidates = []
            for candidate in JsonCheckpointStore(resolved_checkpoint_dir).list_checkpoints():
                try:
                    payload = JsonCheckpointStore(resolved_checkpoint_dir).load_payload(candidate)
                except (OSError, ValueError):
                    continue
                manifest_payload = payload.get("manifest") or {}
                if payload.get("version") == 2 and manifest_payload.get("content_hash") == expected_hash:
                    candidates.append(candidate)
            if candidates:
                load_checkpoint_path = max(candidates, key=lambda item: item.stat().st_mtime_ns)

        resolved_infra = resolve_session_infra(
            agent_data,
            workspace,
            user,
            infra_refs_cli=infra_refs_cli,
            overrides=overrides,
            runtime_refs_cli=runtime_refs_cli,
            anchor=session.manifest_dir or session.original_cwd,
            with_interceptors=True,
        )
        obs_cfg = resolve_observability_config(
            events=events,
            events_file=events_file,
            events_stdout=events_stdout,
            events_format=events_format,
            agent_id=require_agent_id_from_manifest(agent_data),
            manifest=agent_data,
            flavour_spec=flavour_spec,
        )
        try:
            instance, store = instantiate_runtime(
                InstantiationOptions(
                    pattern_plugin_id=plugin,
                    memory_seed_path=_opt_file(memory_seed_path),
                    checkpoint_path=load_checkpoint_path,
                    checkpoint_dir=(
                        resolved_checkpoint_dir if checkpoint_policy.uses_disk() else None
                    ),
                    validate_manifests=not no_validate,
                    cache_read_override=cache_read,
                    cache_write_override=cache_write,
                    cache_read_only=cache_read_only,
                    stream_override=stream,
                    hitl_contract=hitl_contract,
                    user_io_contract=user_io_contract,
                    agent_manifest=agent_data,
                    manifest_dir=session.manifest_dir if manifest else None,
                    resolved_infra=resolved_infra,
                    runtime_refs_cli=runtime_refs_cli,
                    workspace=workspace,
                    enable_observability=not without_obs,
                    enable_governance=not without_gov,
                    model_override=model,
                    events_file=obs_cfg.events_file,
                ),
                hitl=hitl_responder,
            )
        except RuntimeError as exc:
            click.echo(f"error: {exc}", err=True)
            raise SystemExit(1) from None

        if checkpoint_policy.resolved_storage() == "memory":
            from mas.ctl.adapters.checkpoint import InMemoryCheckpointStore

            if load_checkpoint_path is None:
                store = InMemoryCheckpointStore()
                controller_store = store
            else:
                controller_store = store
        else:
            controller_store = store

        obs_rec = setup_observability(
            instance,
            obs_cfg,
            base_dir=session.manifest_dir if manifest else session.original_cwd,
        )

        from mas.ctl.session.session_flags import validate_chat_session

        validate_chat_session(
            interactive=interactive,
            single_turn=single_turn,
            scripted_turns=scripted,
            manifest=agent_data,
        )

        agent_name = agent_data.get("metadata", {}).get("name", "n/a") if agent_data else "n/a"
        if agent_name == "agent" and not manifest:
            # CLI-only run without explicit manifest: show "n/a" instead of generic "agent"
            agent_name = "n/a"

        from mas.runtime.boundary.obs.exchange_hub import ExchangeHub
        from mas.runtime.driver.driver import engine_model_id

        llm_name = engine_model_id(getattr(instance.driver, "engine", None))
        exchange_hub = ExchangeHub() if not no_control else None

        controller = SessionController(
            instance=instance,
            display=display,
            hitl_terminal=hitl_terminal,
            checkpoint_store=controller_store,
            verbose=verbose,
            **trace.as_session_kwargs(),
            obs_recorder=obs_rec,
            agent_id=agent_name,
            llm_id=llm_name,
            exchange_hub=exchange_hub,
            config=ConversationConfig(
                single_turn=single_turn or (bool(scripted) and not interactive),
                save_checkpoint_each_turn=save_checkpoint,
            ),
        )
        session_manager = SessionManager(checkpoint_store=controller_store)
        managed_session = session_manager.create(instance, controller, agent_data or {})
        if load_checkpoint_path is not None:
            session_manager.restore_checkpoint(
                managed_session,
                load_checkpoint_path,
                manifest_content=agent_data or {},
            )
        controller.managed_session = managed_session
        display.on_system(f"session_id: {managed_session.session_id}")

        control_host = None
        if not no_control:
            from mas.ctl.session.control_dir import default_control_dir
            from mas.ctl.session.control_host import ControlDirectoryHost
            from mas.runtime.boundary.control.contract import ControlCapability

            advertised = Path(control_dir) if control_dir else default_control_dir()
            control_host = ControlDirectoryHost(
                session_manager.control(
                    capability=ControlCapability(actor="admin", surface="admin")
                ),
                advertised,
                hub=exchange_hub,
            )
            control_host.start(managed_session.session_id)
            display.on_system(
                "control attach: mas-ctl control attach "
                f"{managed_session.session_id}  ({advertised})"
            )

        if interactive:
            emit_session_protocol_hints(
                interactive=True,
                hitl_terminal=hitl_terminal,
                hitl_responder=hitl_responder,
                verbose=verbose,
                trace=trace.enabled,
                trace_timestamps=trace.timestamps,
                trace_engine=trace.engine,
            )
        from mas.ctl.session.exposure import close_exposures, start_hosted_exposures

        hosted = {} if agent_name in {"", "n/a"} or not agent_data else {str(agent_name): instance}
        exposure_handles = start_hosted_exposures(
            hosted,
            resolved_infra.applications,
            conversation_ids=set(hosted),
            manifests={str(agent_name): agent_data} if hosted else None,
        )
        try:
            rc = run_session_loop(controller, interactive=interactive, scripted=scripted)

            if save_checkpoint and controller_store is not None:
                path = session_manager.checkpoint(managed_session.session_id, label="final")
                display.on_system(f"checkpoint saved: {path}")
        finally:
            close_exposures(exposure_handles)
            if control_host is not None:
                control_host.close()
            close_observability(controller)
        raise SystemExit(rc)


def _apply_debug_script_option(agent_data: dict, value: str, *, manifest_dir) -> None:
    """Install a gdb-like script from ``--debug-script`` (@file or inline)."""
    from mas.library.standard.plugins.control.script import resolve_curl_data

    spec = agent_data.setdefault("spec", {})
    debug: dict[str, str] = {}
    if str(value).startswith("@"):
        debug["script_file"] = str(value)[1:]
    else:
        debug["script"] = resolve_curl_data(value, base_dir=manifest_dir)
    spec["debug"] = debug
    gov = spec.get("governance")
    if isinstance(gov, list):
        spec["governance"] = [
            item
            for item in gov
            if not (isinstance(item, dict) and "debug_script" in item)
            and item not in {"debug_script", "gov_debug_script", "gdb"}
        ]


def _embedded_checkpoint_manifest(path: Path) -> dict | None:
    """Read verified manifest content for resume without a local manifest file."""
    from mas.ctl.adapters.checkpoint import JsonCheckpointStore

    payload = JsonCheckpointStore(path.parent).load_payload(path)
    if payload.get("version") != 2:
        return None
    manifest = payload.get("manifest") or {}
    content = manifest.get("content")
    if not isinstance(content, dict):
        raise click.ClickException("checkpoint has no embedded manifest")
    if ManifestRef.from_content(content).content_hash != manifest.get("content_hash"):
        raise click.ClickException("checkpoint manifest hash mismatch")
    return content
