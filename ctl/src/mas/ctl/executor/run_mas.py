#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""MAS run executor — compose, materialize, session (ctl-owned)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from mas.ctl.compose.runner import ComposeRequest, compose_run
from mas.ctl.deployment.runtime_id import DEFAULT_RUNTIME_ID
from mas.ctl.executor.mas_session import (
    entry_agent_id,
    materialize_mas_compose,
    prepare_delegation_entry_session,
    wire_peer_delegation,
)

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
    trace: bool = False,
    trace_timestamps: bool = False,
    trace_engine: bool = False,
    trace_summary: bool = False,
    trace_color: bool = False,
) -> int:
    """Compose → materialize → SessionController on entry agent."""
    from mas.ctl.session.controller import ConversationConfig, SessionController, close_observability, run_session_loop
    from mas.ctl.session.hitl_config import resolve_hitl_from_manifest
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
    result.bind.hitl_mode = "auto" if (auto_hitl and not interactive) else "block"
    from mas.ctl.session.params_sidecar import (
        apply_runtime_params_to_instance,
        params_from_mas_config,
    )

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

    try:
        prepared = prepare_delegation_entry_session(
            materialized,
            entry_id=entry,
            display=display,
            verbose=verbose,
            trace=trace,
            trace_timestamps=trace_timestamps,
            trace_engine=trace_engine,
            trace_summary=trace_summary,
            trace_color=trace_color,
        )
    except KeyError as exc:
        logger.error("%s", exc)
        return 1

    instance = prepared.instance
    enriched_manifest = prepared.enriched_manifest

    # Wire delegation onto every OTHER agent that declares its own peers too
    # (not just the entry) — see wire_peer_delegation's docstring: without
    # this, an agent that is itself a delegate can never further delegate.
    # Reuses prepared.session_id (minted once, above) so every delegate's
    # own SessionController joins the SAME MAS session instead of each
    # independently starting its own.
    wire_peer_delegation(
        materialized,
        entry_id=entry,
        display=display,
        verbose=verbose,
        already_wired={entry},
        session_id=prepared.session_id,
        working_memory_registry=getattr(prepared, "working_memory_registry", None),
        trace=trace,
        trace_timestamps=trace_timestamps,
        trace_engine=trace_engine,
        trace_summary=trace_summary,
        trace_color=trace_color,
    )

    for agent_instance in materialized.materialized.instances.values():
        apply_runtime_params_to_instance(runtime_params, agent_instance)
    hitl_responder, _ = resolve_hitl_from_manifest(
        enriched_manifest,
        session_interactive=interactive or not auto_hitl,
    )
    if hitl_responder is not None:
        instance.driver.hitl = hitl_responder

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

    a2a_handles = start_materialized_exposures(
        materialized,
        entry_id=entry,
        manifests={str(entry): enriched_manifest},
    )
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
