#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared MAS compose → materialize → session bootstrap (CLI and bench)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml
from mas.ctl.compose.models import EffectiveBindManifest, ResolvedInfra
from mas.ctl.compose.pattern_registry import resolve_design_pattern_registry_id
from mas.ctl.compose.placement_registry import get_placement_backend
from mas.ctl.compose.runner import ComposeResult
from mas.ctl.executor.stacks import ChainHandles, chain_links
from mas.ctl.infra.identities import agency_ids, has_chain, peers_of
from mas.ctl.infra.identities import entry_agent_id as entry_agent_id
from mas.ctl.infra.resolve import application_endpoint_is_used
from mas.ctl.manifest.mas_agent_merge import enrich_entry_agent_for_delegation, wire_entry_engine_delegation
from mas.ctl.session.controller import ConversationConfig, SessionController
from mas.ctl.ui.turn_result import turn_failed, turn_failure_message
from mas.library.standard.lib.stack.handle import RunTurnFn as RunTurnFn
from mas.library.standard.lib.stack.handle import SessionTurn
from mas.runtime.agent_defaults import default_pattern_plugin_id
from mas.runtime.boundary.agentcomm.routing import AgentCommRoute, build_agent_comm_routes
from mas.runtime.boundary.context.working_memory_registry import WorkingMemoryRegistry
from mas.runtime.boundary.delegation.policy import delegation_targets

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _SessionComm:
    """The links a session's agents send with: the default in-process one, and each agent's chain link."""

    local: Any
    handlers: dict[str, Any]
    chain_routes: dict[str, dict[str, AgentCommRoute]]

    def comm_for(self, agent_id: str) -> Any:
        return self.handlers.get(agent_id, self.local)


def _session_comm(
    materialized: MaterializedMas, registry: Any, *, run_turn: RunTurnFn, local_ids: list[str]
) -> _SessionComm:
    """The library default ``agent_comm`` link, plus each agent's links on the Flavour's chains."""
    compose = materialized.compose
    return open_session_comm(
        registry,
        compose.resolved_infra or ResolvedInfra(),
        compose.mas_config,
        run_turn=run_turn,
        local_ids=local_ids,
        handles=materialized.stack_handles,
    )


def open_session_comm(
    registry: Any,
    resolved: ResolvedInfra,
    mas_config: dict[str, Any] | None,
    *,
    run_turn: RunTurnFn,
    local_ids: list[str],
    handles: ChainHandles,
) -> _SessionComm:
    """The links every entry point (run-mas, chat, tui, benchmark runs) gives its local agents.

    The chains opened for them go into ``handles``; the caller closes it when the session ends.
    """
    local = registry.create("agent_comm", binding=None, run_turn=run_turn)
    if not has_chain(resolved):
        return _SessionComm(local, {}, {})
    agent_ids = list(dict.fromkeys([*local_ids, *agency_ids(mas_config)]))
    handlers, routes = chain_links(
        registry,
        resolved,
        run_turn=run_turn,
        local_ids=local_ids,
        handles=handles,
        peers=peers_of(mas_config, agent_ids),
    )
    return _SessionComm(local, handlers, routes)


def open_single_agent_chains(
    instance: Any, agent_id: str, resolved: ResolvedInfra | None, *, display: Any = None, verbose: int = 0
) -> ChainHandles:
    """Link a one-agent session on the Flavour's chains, as run-mas links each of its agents.

    The agent delegates to no one, so it joins a chain only when its
    Application row serves it (``listen``); requests that arrive run a turn on
    ``instance``. With no chain bound this opens nothing.
    """
    from mas.runtime.registry import get_registry

    handles = ChainHandles()
    if not has_chain(resolved):
        return handles
    run_turn = make_workflow_send(
        SimpleNamespace(instances={agent_id: instance}, bus=None), display=display, verbose=verbose
    )
    try:
        open_session_comm(get_registry(), resolved, None, run_turn=run_turn, local_ids=[agent_id], handles=handles)
    except Exception:
        handles.close()
        raise
    return handles


@dataclass(frozen=True)
class MaterializedMas:
    """Compose output plus materialized runtime instances."""

    compose: ComposeResult
    materialized: Any
    mas_base_dir: Path
    # The Flavour chains the session opened; the run closes them when it ends.
    stack_handles: ChainHandles = field(default_factory=ChainHandles)


@dataclass(frozen=True)
class PreparedEntrySession:
    """Entry agent instance with delegation wiring applied."""

    instance: Any
    enriched_manifest: dict[str, Any]
    manifest_path: Path
    entry_agent_id: str
    # Minted here if the caller didn't already have one — this IS "the first
    # request to the first agent of the MAS" (prepare_delegation_entry_session
    # runs once, before the entry agent's own first turn). The caller must
    # reuse this same value for the entry agent's own SessionController and
    # for wire_peer_delegation, so every agent in the run shares it.
    session_id: str = ""
    working_memory_registry: WorkingMemoryRegistry | None = None


def agent_manifest_label(manifest: dict[str, Any], manifest_path: Path) -> str:
    """Resolve agent id for observability from OASF agent doc or bench runtime dict."""
    meta = manifest.get("metadata") or {}
    if meta.get("name"):
        return str(meta["name"])
    mas = manifest.get("mas") or {}
    if mas.get("entry_agent"):
        return str(mas["entry_agent"])
    for row in manifest.get("agents") or []:
        if isinstance(row, dict):
            label = row.get("name") or row.get("id")
            if label:
                return str(label)
    resolved = entry_agent_id(manifest)
    if resolved != "agent":
        return resolved
    return manifest_path.stem or "agent"


def agent_manifest_path(bind: EffectiveBindManifest, agent_id: str) -> Path | None:
    for agent in bind.agents:
        if agent.agent_id != agent_id:
            continue
        manifest_path = agent.manifest_path
        if not manifest_path:
            return None
        mp = Path(manifest_path)
        if not mp.is_absolute():
            base = bind.mas_base_dir or Path.cwd()
            mp = (base / mp).resolve()
        return mp
    return None


def load_agent_manifest_from_bind(bind: EffectiveBindManifest, agent_id: str) -> dict[str, Any] | None:
    mp = agent_manifest_path(bind, agent_id)
    if mp is None or not mp.is_file():
        return None
    doc = yaml.safe_load(mp.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        return None
    composed = bind.composed_application
    if composed is None:
        return doc
    from mas.ctl.manifest.mas_agent_merge import apply_loaded_agent_patch

    return apply_loaded_agent_patch(
        doc,
        agent_id=agent_id,
        agent_patches=composed.agent_patches,
        mas_config=composed.config,
    )


def resolve_entry_pattern_plugin_id(
    compose: ComposeResult | None,
    entry_id: str,
    *,
    entry_manifest: dict[str, Any] | None = None,
) -> str:
    """Pattern plugin id from compose bind slice, else merged entry manifest."""
    if compose is not None:
        for agent in compose.bind.agents:
            if agent.agent_id == entry_id:
                return agent.pattern_plugin_id
    if entry_manifest:
        spec = entry_manifest.get("spec") or {}
        dp = spec.get("design_pattern")
        if isinstance(dp, dict):
            return resolve_design_pattern_registry_id(dp)
    return default_pattern_plugin_id()


def materialize_mas_compose(compose: ComposeResult, *, mas_base_dir: Path | None = None) -> MaterializedMas:
    base = mas_base_dir or compose.bind.mas_base_dir or Path.cwd()
    materialized = get_placement_backend(compose.plan.strategy).materialize(compose.bind, compose.plan)
    return MaterializedMas(compose=compose, materialized=materialized, mas_base_dir=base)


def prepare_delegation_entry_session(
    materialized: MaterializedMas,
    *,
    entry_id: str,
    entry_manifest: dict[str, Any] | None = None,
    entry_manifest_path: Path | None = None,
    display: Any = None,
    verbose: int = 0,
    session_id: str = "",
    trace: bool = False,
    trace_timestamps: bool = False,
    trace_engine: bool = False,
    trace_summary: bool = False,
    trace_color: bool = False,
) -> PreparedEntrySession:
    """Wire dynamic-delegation entry agent (same path as ``execute_run_mas``).

    Mints ``session_id`` if the caller doesn't already have one — this is
    "the first request to the first agent" for a fresh MAS run, since this
    runs once, before the entry agent's own first turn. The caller must
    reuse the returned ``PreparedEntrySession.session_id`` for the entry
    agent's own ``SessionController`` and for ``wire_peer_delegation``, so
    every agent shares the same session.
    """
    compose = materialized.compose
    instance = materialized.materialized.instances.get(entry_id)
    if instance is None:
        raise KeyError(f"entry agent {entry_id!r} not materialized (have: {list(materialized.materialized.instances)})")

    if hasattr(instance.driver, "agent_id"):
        instance.driver.agent_id = entry_id

    resolved_session_id = session_id or str(uuid.uuid4())
    working_memory_registry = WorkingMemoryRegistry()
    materialized.materialized._session_working_memory_registry = working_memory_registry

    manifest_path = entry_manifest_path or agent_manifest_path(compose.bind, entry_id)
    agent_manifest = entry_manifest or load_agent_manifest_from_bind(compose.bind, entry_id) or {}
    entry_manifest_dir = manifest_path.parent if manifest_path else materialized.mas_base_dir

    enriched = enrich_entry_agent_for_delegation(
        agent_manifest,
        compose.mas_config,
        manifest_dir=entry_manifest_dir,
        mas_base_dir=materialized.mas_base_dir,
    )
    from mas.ctl.executor.spawn_ledger import SpawnLedger
    from mas.ctl.executor.subagent_spawner import ledger_from_engine
    from mas.runtime.engine.tools import spawn_subagent_params

    spawn_params = spawn_subagent_params(enriched.get("spec")) or {}
    engine = getattr(getattr(instance, "driver", None), "engine", None)
    ledger = ledger_from_engine(engine)
    if ledger is None:
        ledger = SpawnLedger(
            max_depth=spawn_params.get("max_depth", 3),
            max_spawns=spawn_params.get("max_spawns", 8),
        )
    materialized.materialized._subagent_spawn_ledger = ledger
    from mas.runtime.registry import get_registry

    registry = get_registry()
    run_turn = make_workflow_send(
        materialized.materialized,
        display=display,
        verbose=verbose,
        from_agent=entry_id,
        session_id=resolved_session_id,
        working_memory_registry=working_memory_registry,
        trace=trace,
        trace_timestamps=trace_timestamps,
        trace_engine=trace_engine,
        trace_summary=trace_summary,
        trace_color=trace_color,
    )
    comms = _session_comm(materialized, registry, run_turn=run_turn, local_ids=[entry_id])
    peers = delegation_targets(enriched, agent_id=entry_id)
    wire_entry_engine_delegation(
        getattr(getattr(instance, "driver", None), "engine", None),
        enriched,
        entry_manifest_dir,
        comm=comms.comm_for(entry_id),
        entry_agent_id=entry_id,
        mas_config=compose.mas_config,
        mas_base_dir=materialized.mas_base_dir,
        routes=comms.chain_routes.get(entry_id),
        peers=peers or None,
    )
    from mas.ctl.executor.subagent_spawner import wire_subagent_spawning

    wire_subagent_spawning(
        getattr(instance.driver, "engine", None),
        materialized=materialized.materialized,
        manifest=enriched,
        manifest_dir=entry_manifest_dir,
        parent_agent_id=entry_id,
        session_id=resolved_session_id,
        working_memory_registry=working_memory_registry,
        display=display,
        verbose=verbose,
        flavour_spec=compose.bind.flavour_spec,
        parent_spec=compose.mas_config,
        entry_id=entry_id,
        delegate_ids=tuple(peers or ()),
    )
    return PreparedEntrySession(
        instance=instance,
        enriched_manifest=enriched,
        manifest_path=manifest_path or entry_manifest_dir / f"{entry_id}.yaml",
        entry_agent_id=entry_id,
        session_id=resolved_session_id,
        working_memory_registry=working_memory_registry,
    )


def wire_peer_delegation(
    materialized: MaterializedMas,
    *,
    entry_id: str,
    display: Any = None,
    verbose: int = 0,
    already_wired: "set[str] | None" = None,
    session_id: str = "",
    working_memory_registry: WorkingMemoryRegistry | None = None,
    trace: bool = False,
    trace_timestamps: bool = False,
    trace_engine: bool = False,
    trace_summary: bool = False,
    trace_color: bool = False,
) -> list[str]:
    """Wire delegation onto every agent that declares its own ``delegates_to``
    peers in the MAS workflow topology — not just the entry agent.

    ``prepare_delegation_entry_session`` only ever wires the entry agent's
    engine (``wire_entry_engine_delegation`` called once). An agent that is
    itself a delegate (e.g. ``schedule_agent``, delegated to by
    ``moderator``) never gets its own ``.delegation`` set — if IT tries to
    further delegate (``delegate_to_concierge_agent``), the call falls
    through to the manifest tool-provider path and fails outright with
    ``ToolExecutionError``: nested/multi-level delegation silently doesn't
    work today. ``delegation_targets`` (runtime's delegation policy) already
    supports resolving peers for *any* agent_id in the workflow topology —
    only the wiring here was hardcoded to the entry agent.

    Safe to call with a single shared ``run_turn`` closure across every
    delegating agent: parent-call linkage is now a real, explicit
    ``caller_call_id`` threaded through the delegation contract (see
    ``InvokeEngineIo.call_id`` / ``execute_engine_tool`` /
    ``DelegationContract``), resolved fresh at each dispatch — it no longer
    depends on which closure or which ``from_agent`` was captured at
    wiring time.

    Returns the list of agent_ids newly wired (for logging/testing).
    """
    from mas.runtime.boundary.delegation.policy import delegation_targets

    compose = materialized.compose
    wired: set[str] = set(already_wired or ())
    run_turn = make_workflow_send(
        materialized.materialized,
        display=display,
        verbose=verbose,
        from_agent=entry_id,
        session_id=session_id,
        working_memory_registry=working_memory_registry,
        trace=trace,
        trace_timestamps=trace_timestamps,
        trace_engine=trace_engine,
        trace_summary=trace_summary,
        trace_color=trace_color,
    )
    spec = compose.mas_config.get("spec") or compose.mas_config
    agency_agents = (spec.get("agency") or {}).get("agents") or []
    from mas.runtime.registry import get_registry

    registry = get_registry()
    resolved = compose.resolved_infra or ResolvedInfra()
    local_ids = list(materialized.materialized.instances)
    comms = _session_comm(materialized, registry, run_turn=run_turn, local_ids=local_ids)
    peer_routes: dict[str, AgentCommRoute] = {}
    for peer in agency_agents:
        if not isinstance(peer, dict):
            continue
        name = str(peer.get("name") or peer.get("id") or "").strip()
        if not name:
            raise ValueError("spec.agency.agents entries require id")
        binding = resolved.applications.get(name)
        if not isinstance(binding, dict) or not application_endpoint_is_used(binding):
            continue
        kind = str(binding.get("protocol") or binding.get("kind") or "").strip()
        params = {key: binding[key] for key in ("url", "headers", "timeout") if key in binding}
        if "url" not in params:
            raise ValueError(f"infra Application endpoint for agent {name!r} requires url")
        handler = registry.create("agent_comm", binding={"type": kind, "params": params})
        peer_routes[name] = AgentCommRoute(name, kind, handler)

    chained = {name for routes in comms.chain_routes.values() for name in routes}
    delegated_names = {
        str(target)
        for node in ((spec.get("workflow") or {}).get("nodes") or [])
        if isinstance(node, dict)
        for target in (node.get("delegates_to") or [])
        if str(target).strip()
    }
    # Every agent can reach the in-process agents and the peers it names; a chain route replaces the
    # local one for an agent on the chain.
    base_routes = build_agent_comm_routes(
        local_agent_ids=local_ids,
        local_handler=comms.local,
        peer_routes=peer_routes,
        delegated_names=delegated_names - chained,
    )

    def routes_for(agent_id: str) -> dict[str, AgentCommRoute]:
        return {**base_routes, **(comms.chain_routes.get(agent_id) or {})}

    newly_wired: list[str] = []
    for agent in compose.bind.agents:
        agent_id = agent.agent_id
        instance = materialized.materialized.instances.get(agent_id)
        if agent_id in wired:
            if instance is not None:
                engine = getattr(getattr(instance, "driver", None), "engine", None)
                from mas.runtime.engine.leaf import leaf_engine

                leaf = leaf_engine(engine)
                delegation = getattr(leaf, "delegation", None)
                if hasattr(delegation, "set_routes"):
                    delegation.set_routes(routes_for(agent_id))
                manifest = (
                    getattr(leaf, "manifest", None) or load_agent_manifest_from_bind(compose.bind, agent_id) or {}
                )
                manifest_path = agent_manifest_path(compose.bind, agent_id)
                from mas.ctl.executor.subagent_spawner import wire_subagent_spawning

                wire_subagent_spawning(
                    engine,
                    materialized=materialized.materialized,
                    manifest=manifest,
                    manifest_dir=manifest_path.parent if manifest_path else materialized.mas_base_dir,
                    parent_agent_id=agent_id,
                    session_id=session_id,
                    working_memory_registry=working_memory_registry,
                    display=display,
                    verbose=verbose,
                    flavour_spec=compose.bind.flavour_spec,
                    parent_spec=compose.mas_config,
                    entry_id=entry_id,
                )
            continue
        if instance is None:
            continue
        agent_manifest = load_agent_manifest_from_bind(compose.bind, agent_id) or {}
        manifest_path = agent_manifest_path(compose.bind, agent_id)
        manifest_dir = manifest_path.parent if manifest_path else materialized.mas_base_dir
        enriched = enrich_entry_agent_for_delegation(
            agent_manifest,
            compose.mas_config,
            manifest_dir=manifest_dir,
            mas_base_dir=materialized.mas_base_dir,
        )
        from mas.ctl.executor.subagent_spawner import wire_subagent_spawning

        wire_subagent_spawning(
            getattr(getattr(instance, "driver", None), "engine", None),
            materialized=materialized.materialized,
            manifest=enriched,
            manifest_dir=manifest_dir,
            parent_agent_id=agent_id,
            session_id=session_id,
            working_memory_registry=working_memory_registry,
            display=display,
            verbose=verbose,
            flavour_spec=compose.bind.flavour_spec,
            parent_spec=compose.mas_config,
            entry_id=entry_id,
        )
        peers = delegation_targets(enriched, agent_id=agent_id)
        if not peers:
            continue
        wire_entry_engine_delegation(
            getattr(getattr(instance, "driver", None), "engine", None),
            enriched,
            manifest_dir,
            comm=comms.comm_for(agent_id),
            entry_agent_id=agent_id,
            mas_config=compose.mas_config,
            mas_base_dir=materialized.mas_base_dir,
            routes=routes_for(agent_id),
            peers=peers,
        )
        wired.add(agent_id)
        newly_wired.append(agent_id)
    return newly_wired


def make_workflow_send(
    materialized: Any,
    *,
    display: Any,
    verbose: int,
    from_agent: str = "",
    session_id: str = "",
    working_memory_registry: WorkingMemoryRegistry | None = None,
    trace: bool = False,
    trace_timestamps: bool = False,
    trace_engine: bool = False,
    trace_summary: bool = False,
    trace_color: bool = False,
) -> SessionTurn:
    """Run one agent turn inside a multi-agent workflow (sequential or delegation).

    The returned ``SessionTurn`` (``send``, and ``asend`` to await it) holds mutable routing
    state and is **not reentrant**; use one per workflow run on a single thread.

    ``session_id`` (empty means mint a fresh one — the caller has none yet)
    is captured once here and reused for every ``send()`` call this closure
    ever makes: this is what puts session_id "on the wire" for delegation —
    each delegate's own ``SessionController`` is constructed with this same
    value, so a delegate's first-ever task still belongs to the entry
    agent's session rather than starting a new one.
    """
    state = {
        "prev_agent": from_agent,
        "correlation_id": 0,
        "call_seq": 0,
        "session_id": session_id or str(uuid.uuid4()),
        "controllers": {},
        "working_memory_registry": working_memory_registry or WorkingMemoryRegistry(),
    }

    def send(
        agent_id: str,
        prompt: str,
        delegate_correlation_id: int = 0,
        caller_call_id: str = "",
        context_id: str = "",
    ) -> str:
        bus = getattr(materialized, "bus", None)
        prev_agent = state["prev_agent"]
        # caller_call_id arrives already resolved — the driver attaches it to
        # the delegating TOOL_CALL's own InvokeEngineIo (see
        # InvokeEngineIo.call_id) before the engine ever runs, and it's
        # threaded explicitly through execute_engine_tool -> LlmDelegator ->
        # this closure (RunTurnFn's 4th argument) — a real contract field,
        # not a lookup reconstructed here after the fact. This is what makes
        # nested/multi-level delegation work too: whichever agent is
        # delegating (entry or not) already resolved its own call_id at
        # dispatch time, so this closure no longer needs to assume the
        # delegator is always ``from_agent`` or reach into
        # materialized.instances to find it.
        parent_call_id = caller_call_id
        # RuntimeInstance.run_user_text builds this invocation's own exec_id
        # as f"{agent_id}-{turn_id}-exec" (instance.py). turn_id is the
        # unique caller_call_id (or a per-closure sequence number), so two
        # calls to the same agent stay distinct even when we reuse one
        # SessionController — that reuse is required so CliTraceExchangePlugin
        # is subscribed once. Recreating the controller every send() stacked
        # a new plugin on the shared driver and reprinted every later reply
        # N times with non-monotonic +offsets.
        state["call_seq"] += 1
        turn_id = caller_call_id or f"{agent_id}-seq{state['call_seq']}"
        if bus is not None and prev_agent and prev_agent != agent_id:
            from mas.runtime.schema.egress import InvokeEngineIo

            state["correlation_id"] += 1
            bus.send(
                from_agent=prev_agent,
                to_agent=agent_id,
                intent=InvokeEngineIo(correlation_id=state["correlation_id"], op="TRANSPORT_MSG"),
            )
        instance = materialized.instances.get(agent_id)
        if instance is None:
            raise KeyError(f"agent {agent_id!r} not materialized (have: {list(materialized.instances)})")
        if hasattr(instance.driver, "agent_id"):
            instance.driver.agent_id = agent_id
        # An explicit context_id (LLM-chosen, via the delegate tool's optional
        # context_id argument) picks a working-memory bucket independent of
        # the ambient session_id — lets the delegating agent run more than
        # one context with the same peer inside one session. Omitted (the
        # common case) falls back to the session's default bucket. Passed to
        # SessionController below as working_memory_key -- the controller
        # syncs working memory itself (same mechanism a directly-driven chat
        # session uses), so this closure doesn't sync it a second time.
        memory_key = context_id or state["session_id"]
        sub_display = display
        # A turn another agent asked for (a delegation, or a request that arrived over a chain, which
        # carries the caller's call id) is not the user's: show it under the agent's label, not "You:".
        if from_agent and (agent_id != from_agent or parent_call_id):
            import sys

            from mas.ctl.ui.stdout import StdoutConversationDisplay

            base = display if isinstance(display, StdoutConversationDisplay) else None
            sub_display = StdoutConversationDisplay(
                out=getattr(base, "_out", sys.stdout),
                err=getattr(base, "_err", sys.stderr),
                agent_label=agent_id,
                verbose=verbose,
                show_labels=True,
                user_prompt_echoed=True,
                trace=getattr(base, "_trace", False),
            )
        caller_agent_id = from_agent if parent_call_id and from_agent and from_agent != agent_id else ""
        controllers: dict[str, Any] = state["controllers"]
        controller = controllers.get(agent_id)
        if controller is None:
            controller = SessionController(
                instance=instance,
                display=sub_display,
                verbose=verbose,
                agent_id=agent_id,
                config=ConversationConfig(single_turn=True),
                session_id=state["session_id"],
                working_memory_key=memory_key,
                working_memory_registry=state["working_memory_registry"],
                caller_agent_id=caller_agent_id,
                trace=trace,
                trace_timestamps=trace_timestamps,
                trace_engine=trace_engine,
                trace_summary=trace_summary,
                trace_color=trace_color,
            )
            controllers[agent_id] = controller
        else:
            controller.caller_agent_id = caller_agent_id
            controller.working_memory_key = memory_key
            controller.display = sub_display
        result = controller.run_turn(prompt, turn_id=turn_id, parent_call_id=parent_call_id)
        # Do NOT close observability after a delegated sub-turn: in a multi-agent
        # run every agent shares one plugin set owned by the top-level session
        # (see setup_shared_obs), and the sub-agent instance now references it.
        # Closing here would tear the shared events sink down mid-run and
        # truncate the entry agent's post-delegation events (e.g. its final
        # synthesis llm_call_end).  The top-level owner closes it once at the end.
        # Only close a controller that owns its own recorder (non-shared setups).
        if controller.obs_recorder is not None:
            from mas.ctl.session.controller import close_observability

            close_observability(controller)
        state["prev_agent"] = agent_id
        if turn_failed(result):
            raise RuntimeError(turn_failure_message(result, agent_id))

        # Propagate awaiting_hitl state via side channel (not return value)
        # This allows external clients to detect and resolve HITL requests
        # from delegated agents without breaking the delegation contract.
        if result.awaiting_hitl:
            from mas.runtime.boundary.hitl.registry import get_hitl_resolver_registry

            registry = get_hitl_resolver_registry()
            # Check if there are any pending HITL requests for this agent
            if registry.has_pending(state["session_id"], agent_id):
                # Mark in state that this agent has pending HITL
                state.setdefault("pending_hitl_agents", set()).add(agent_id)

        return result.text

    async def asend(
        agent_id: str,
        prompt: str,
        delegate_correlation_id: int = 0,
        caller_call_id: str = "",
        context_id: str = "",
    ) -> str:
        bus = getattr(materialized, "bus", None)
        prev_agent = state["prev_agent"]
        parent_call_id = caller_call_id
        state["call_seq"] += 1
        turn_id = caller_call_id or f"{agent_id}-seq{state['call_seq']}"
        if bus is not None and prev_agent and prev_agent != agent_id:
            from mas.runtime.schema.egress import InvokeEngineIo

            state["correlation_id"] += 1
            bus.send(
                from_agent=prev_agent,
                to_agent=agent_id,
                intent=InvokeEngineIo(correlation_id=state["correlation_id"], op="TRANSPORT_MSG"),
            )
        instance = materialized.instances.get(agent_id)
        if instance is None:
            raise KeyError(f"agent {agent_id!r} not materialized (have: {list(materialized.instances)})")
        if hasattr(instance.driver, "agent_id"):
            instance.driver.agent_id = agent_id
        memory_key = context_id or state["session_id"]
        sub_display = display
        # A turn another agent asked for (a delegation, or a request that arrived over a chain, which
        # carries the caller's call id) is not the user's: show it under the agent's label, not "You:".
        if from_agent and (agent_id != from_agent or parent_call_id):
            import sys

            from mas.ctl.ui.stdout import StdoutConversationDisplay

            base = display if isinstance(display, StdoutConversationDisplay) else None
            sub_display = StdoutConversationDisplay(
                out=getattr(base, "_out", sys.stdout),
                err=getattr(base, "_err", sys.stderr),
                agent_label=agent_id,
                verbose=verbose,
                show_labels=True,
                user_prompt_echoed=True,
                trace=getattr(base, "_trace", False),
            )
        caller_agent_id = from_agent if parent_call_id and from_agent and from_agent != agent_id else ""
        controllers: dict[str, Any] = state["controllers"]
        controller = controllers.get(agent_id)
        if controller is None:
            controller = SessionController(
                instance=instance,
                display=sub_display,
                verbose=verbose,
                agent_id=agent_id,
                config=ConversationConfig(single_turn=True),
                session_id=state["session_id"],
                working_memory_key=memory_key,
                working_memory_registry=state["working_memory_registry"],
                caller_agent_id=caller_agent_id,
                trace=trace,
                trace_timestamps=trace_timestamps,
                trace_engine=trace_engine,
                trace_summary=trace_summary,
                trace_color=trace_color,
            )
            controllers[agent_id] = controller
        else:
            controller.caller_agent_id = caller_agent_id
            controller.working_memory_key = memory_key
            controller.display = sub_display
        result = await controller.arun_turn(prompt, turn_id=turn_id, parent_call_id=parent_call_id)
        if controller.obs_recorder is not None:
            from mas.ctl.session.controller import close_observability

            close_observability(controller)
        state["prev_agent"] = agent_id
        if turn_failed(result):
            raise RuntimeError(turn_failure_message(result, agent_id))

        if result.awaiting_hitl:
            from mas.runtime.boundary.hitl.registry import get_hitl_resolver_registry

            registry = get_hitl_resolver_registry()
            if registry.has_pending(state["session_id"], agent_id):
                state.setdefault("pending_hitl_agents", set()).add(agent_id)

        return result.text

    return SessionTurn(send, asend)
