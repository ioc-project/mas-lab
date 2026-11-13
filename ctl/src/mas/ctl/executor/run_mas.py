#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""MAS run executor — compose, materialize, session (ctl-owned)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mas.ctl.compose.runner import ComposeRequest, compose_run
from mas.ctl.deployment.runtime_id import DEFAULT_RUNTIME_ID
from mas.ctl.executor.mas_session import (
    entry_agent_id,
    materialize_mas_compose,
    prepare_delegation_entry_session,
    wire_peer_delegation,
)
from mas.ctl.executor.stacks import ChainStartError

if TYPE_CHECKING:
    from mas.runtime.boundary.obs.plugins import ObsPluginSet

logger = logging.getLogger(__name__)


def _log_obs_output_paths(plugin_set: "ObsPluginSet") -> None:
    for plugin in plugin_set.plugins:
        get_paths = getattr(plugin, "output_file_paths", None)
        if callable(get_paths):
            for p in get_paths():
                logger.info("events: %s", p)
        else:
            for em in getattr(plugin, "emitters", []):
                path = getattr(em, "path", None)
                if path:
                    logger.info("events: %s", path)


@dataclass(frozen=True)
class _EntrySession:
    """What opening the entry agent's session needs, apart from the materialized MAS."""

    entry: str
    declared: str
    delegates: list[str]
    runtime_params: Any
    flavour_spec: dict
    session_interactive: bool
    verbose: int = 0
    trace: bool = False
    trace_timestamps: bool = False
    trace_engine: bool = False
    trace_summary: bool = False
    trace_color: bool = False


class _QuietDisplay:
    """Display for a conversation nobody watches in a terminal."""

    def __getattr__(self, name: str) -> Any:
        return lambda *args, **kwargs: None


def open_entry_session(materialized: Any, args: _EntrySession, *, display: Any) -> Any:
    """Wire the entry agent of a materialized MAS: delegation, peers, params, governance, HITL.

    Used for the run's own session and for every A2A conversation, so a
    conversation is wired exactly like the run.
    """
    from mas.ctl.session.flavour_control import apply_flavour_controls
    from mas.ctl.session.hitl_config import resolve_hitl_from_manifest
    from mas.ctl.session.params_sidecar import apply_runtime_params_to_instance

    prepared = prepare_delegation_entry_session(
        materialized,
        entry_id=args.entry,
        display=display,
        verbose=args.verbose,
        trace=args.trace,
        trace_timestamps=args.trace_timestamps,
        trace_engine=args.trace_engine,
        trace_summary=args.trace_summary,
        trace_color=args.trace_color,
    )
    # Wire delegation onto every OTHER agent that declares its own peers too
    # (not just the entry): without it an agent that is itself a delegate can
    # never delegate further. Reuses prepared.session_id so every delegate's
    # own SessionController joins the SAME MAS session.
    wire_peer_delegation(
        materialized,
        entry_id=args.entry,
        display=display,
        verbose=args.verbose,
        already_wired={args.entry},
        session_id=prepared.session_id,
        working_memory_registry=getattr(prepared, "working_memory_registry", None),
        trace=args.trace,
        trace_timestamps=args.trace_timestamps,
        trace_engine=args.trace_engine,
        trace_summary=args.trace_summary,
        trace_color=args.trace_color,
    )
    for agent_instance in materialized.materialized.instances.values():
        apply_runtime_params_to_instance(args.runtime_params, agent_instance)
    # The entry agent's governance as the Controls give it (warnings print once, at instantiation).
    apply_flavour_controls(
        prepared.enriched_manifest,
        args.flavour_spec,
        agent_id=args.entry,
        entry_id=args.declared,
        delegate_ids=args.delegates,
        warn=False,
    )
    hitl_responder, _ = resolve_hitl_from_manifest(
        prepared.enriched_manifest,
        session_interactive=args.session_interactive,
        flavour_spec=args.flavour_spec,
        agent_id=args.entry,
        entry_id=args.declared,
        delegate_ids=args.delegates,
    )
    if hitl_responder is not None:
        prepared.instance.driver.hitl = hitl_responder
    return prepared


def execute_run_mas(
    manifest: Path,
    *,
    prompt: str | None = None,
    queries: list[str] | None = None,
    overlay_paths: list[Path] | None = None,
    overlay_ids: list[str] | None = None,
    overrides: list[str] | None = None,
    infra_refs: list[str] | None = None,
    middleware_refs: list[str] | None = None,
    deployment_path: Path | None = None,
    kernel_backend: str = DEFAULT_RUNTIME_ID,
    single_turn: bool = False,
    interactive: bool = False,
    auto_hitl: bool = True,
    validate: bool = True,
    verbose: int = 0,
    manifest_dir: Path | None = None,
    obs_config=None,
    events: bool | None = None,
    events_file: str | None = None,
    events_stdout: bool = False,
    events_format: str | None = None,
    trace: bool = False,
    trace_timestamps: bool = False,
    trace_engine: bool = False,
    trace_summary: bool = False,
    trace_color: bool = False,
    flavour_spec: dict | None = None,
) -> int:
    """Compose → materialize → SessionController on entry agent.

    ``flavour_spec`` is the resolved Flavour ``spec``; its ``bind.agent_comm``
    names the infra rows agent-to-agent calls ride.
    """
    from mas.ctl.session.controller import ConversationConfig, SessionController, close_observability, run_session_loop
    from mas.ctl.ui.stdout import StdoutConversationDisplay

    scripted = list(queries or [])
    if prompt:
        scripted.insert(0, prompt)

    req = ComposeRequest(
        manifest=manifest,
        deployment_path=deployment_path,
        overlay_ids=list(overlay_ids or []),
        overlay_paths=list(overlay_paths or []),
        overrides=list(overrides or []),
        infra_refs=list(infra_refs or []),
        middleware_refs=list(middleware_refs or []),
        kernel_backend=kernel_backend,
        validate=validate,
    )
    result = compose_run(req)
    from mas.ctl.infra.bind import BindError, bind_session
    from mas.ctl.infra.identities import agency_ids, declared_entry_id, peers_of
    from mas.ctl.session.flavour_control import ControlError, attach_overlay_controls

    ids = agency_ids(result.mas_config)
    declared = declared_entry_id(result.mas_config)
    delegates = list(peers_of(result.mas_config, ids).get(declared) or [])
    if not isinstance(flavour_spec, dict):
        flavour_spec = {}
    try:
        attach_overlay_controls(flavour_spec, overlay_paths or [])
        result.resolved_infra = bind_session(result.resolved_infra, flavour_spec, agent_ids=ids)
        result.bind.flavour_spec = flavour_spec
    except (BindError, ControlError) as exc:
        logger.error("%s", exc)
        return 1
    if obs_config is None:
        from mas.ctl.cli.obs_flags import resolve_observability_config

        deployment_doc = None
        if deployment_path is not None:
            import yaml

            deployment_doc = yaml.safe_load(Path(deployment_path).read_text(encoding="utf-8"))
        obs_config = resolve_observability_config(
            events=events,
            events_file=events_file,
            events_stdout=events_stdout,
            events_format=events_format,
            manifest=result.mas_config,
            deployment=deployment_doc,
            flavour_spec=flavour_spec,
            resolved_infra=result.resolved_infra,
            entry_id=declared,
        )
    result.bind.hitl_mode = "auto" if (auto_hitl and not interactive) else "block"
    from mas.ctl.session.params_sidecar import params_from_mas_config

    runtime_params = params_from_mas_config(result.mas_config)

    materialized = materialize_mas_compose(result, mas_base_dir=manifest_dir or manifest.parent)

    if getattr(materialized.materialized, "bus", None) is not None:
        logger.debug(
            "materialized run has in-process bus with %d routes",
            len(getattr(materialized.materialized.bus, "_endpoints", {}) or {}),
        )

    if interactive and verbose == 0:
        verbose = 1

    base = manifest_dir or manifest.parent
    entry = entry_agent_id(result.mas_config)
    display = StdoutConversationDisplay(
        agent_label=str(entry or "Agent"),
        verbose=verbose,
        show_labels=True,
        trace=trace,
    )

    session_args = _EntrySession(
        entry=entry,
        declared=declared,
        delegates=delegates,
        runtime_params=runtime_params,
        flavour_spec=flavour_spec,
        session_interactive=interactive or not auto_hitl,
        verbose=verbose,
        trace=trace,
        trace_timestamps=trace_timestamps,
        trace_engine=trace_engine,
        trace_summary=trace_summary,
        trace_color=trace_color,
    )

    # The chains open while the session is prepared. Whatever fails before the run loop starts closes them.
    try:
        try:
            prepared = open_entry_session(materialized, session_args, display=display)
        except (KeyError, ChainStartError) as exc:
            logger.error("%s", exc)
            materialized.stack_handles.close()
            return 1

        instance = prepared.instance
        enriched_manifest = prepared.enriched_manifest

        from mas.ctl.session.observability import setup_run_observability

        instances = dict(materialized.materialized.instances)
        # Subscribe materialized agents that didn't declare their own
        # spec.observability (see partition_instances_by_observability, FT8) to
        # one shared events.jsonl, not just the entry agent.  Delegated sub-agent
        # turns run through their own SessionController (see make_workflow_send);
        # without a shared plugin set their executions — LLM calls, tools, the
        # whole sub-turn — are never emitted, so delegate_to_* tool calls are
        # opaque black boxes and the multilevel trajectory shows only the
        # moderator.  This mirrors the sequential-workflow path.
        plugin_set, scoped_recorders = setup_run_observability(
            instances,
            obs_config,
            base_dir=base,
            entry_agent_id=str(entry),
        )

        from mas.ctl.session.exposure import close_exposures, start_materialized_exposures

        conversation_plugin_set = plugin_set

        def new_conversation() -> Any:
            """A new A2A conversation: the whole MAS built again, so it shares nothing with this run."""
            fresh = materialize_mas_compose(result, mas_base_dir=manifest_dir or manifest.parent)
            try:
                opened = open_entry_session(fresh, session_args, display=_QuietDisplay())
            except BaseException:
                fresh.stack_handles.close()
                raise
            live = opened.instance
            if conversation_plugin_set is not None and live.driver.observability is not None:
                conversation_plugin_set.subscribe_to(live.driver.observability, agent_id=str(entry))
            live.conversation_close = fresh.stack_handles.close
            return live

        a2a_handles = start_materialized_exposures(
            materialized,
            entry_id=entry,
            manifests={str(entry): enriched_manifest},
            conversation_factories={str(entry): new_conversation},
        )
    except ChainStartError as exc:
        logger.error("%s", exc)
        materialized.stack_handles.close()
        return 1
    except BaseException:
        materialized.stack_handles.close()
        raise

    try:
        controller = SessionController(
            instance=instance,
            display=display,
            verbose=verbose,
            trace=trace,
            trace_timestamps=trace_timestamps,
            trace_engine=trace_engine,
            trace_summary=trace_summary,
            trace_color=trace_color,
            agent_id=str(entry),
            config=ConversationConfig(
                single_turn=single_turn or (bool(scripted) and not interactive),
            ),
            session_id=prepared.session_id,
            working_memory_registry=getattr(prepared, "working_memory_registry", None),
            turn_guard=materialized.stack_handles.turn_lock(str(entry)),
        )
        exit_code = run_session_loop(
            controller,
            interactive=interactive or not auto_hitl,
            scripted=scripted,
        )
        close_observability(controller)
        for recorder in scoped_recorders:
            recorder.close()
        if plugin_set is not None:
            _log_obs_output_paths(plugin_set)
        return exit_code
    finally:
        close_exposures(a2a_handles)
        materialized.stack_handles.close()
