#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Emulation Resolver — maps EmulationSpec to plugin bindings.

Called by the benchmark runner to translate the declarative emulation
posture (experiment YAML) into concrete plugin instances registered on
each agent before execution.

This module is the single point where ``EmulationSpec`` is interpreted.
The benchmark runner calls ``resolve_emulation_plugins()`` and registers
the returned plugins on the MasRuntime's agents.

Example::

    from mas.lab.emulation_resolver import resolve_emulation_plugins

    plugins = resolve_emulation_plugins(exp.bench_emulation)
    for agent in runtime.agents.values():
        for plugin, name, priority in plugins:
            agent.register_plugin(plugin, name=name, priority=priority)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def resolve_emulation_plugins(
    emulation: Any,
    *,
    replay_trace_path: Optional[Path] = None,
    memory_snapshot_path: Optional[Path] = None,
    state_snapshot_path: Optional[Path] = None,
    faults_config: Optional[Dict[str, Any]] = None,
) -> List[Tuple[Any, str, int]]:
    """Resolve an EmulationSpec into a list of (plugin, name, priority) tuples.

    Args:
        emulation: An ``EmulationSpec`` instance (from experiment YAML).
        replay_trace_path: Path to events.jsonl for replay modes.
        memory_snapshot_path: Path to memory snapshot JSON for snapshot/seeded modes.
        state_snapshot_path: Path to state snapshot JSON for snapshot/seeded modes.
        faults_config: Optional dict of fault injection configuration.

    Returns:
        List of (plugin_instance, registration_name, priority) tuples.
        The caller registers each one on the agent(s).
    """
    if emulation is None:
        return []

    plugins: List[Tuple[Any, str, int]] = []
    infra = getattr(emulation, "infra", None)

    # ------------------------------------------------------------------
    # L1: Resource emulation plugin
    # ------------------------------------------------------------------
    if infra is not None:
        llm_mode = getattr(infra, "llm", "live")
        tools_mode = getattr(infra, "tools", "live")
        memory_mode = getattr(infra, "memory", "live")
        embeddings_mode = getattr(infra, "embeddings", "live")
        state_mode = getattr(infra, "state", "live")

        # Only create the plugin if at least one resource is non-live
        if any(m != "live" for m in [llm_mode, tools_mode, memory_mode, embeddings_mode, state_mode]):
            from mas.runtime.plugins.resource_emulation_plugin import ResourceEmulationPlugin

            emul_plugin = ResourceEmulationPlugin(
                llm=llm_mode,
                tools=tools_mode,
                memory=memory_mode,
                embeddings=embeddings_mode,
                state=state_mode,
                replay_trace_path=replay_trace_path,
                memory_snapshot_path=memory_snapshot_path,
                state_snapshot_path=state_snapshot_path,
            )
            plugins.append((emul_plugin, "resource_emulation", 2))
            logger.info(
                "[emulation] Resource emulation: llm=%s tools=%s memory=%s embed=%s state=%s",
                llm_mode, tools_mode, memory_mode, embeddings_mode, state_mode,
            )

    # ------------------------------------------------------------------
    # L1/L3: Fault injection plugin
    # ------------------------------------------------------------------
    if faults_config:
        from mas.runtime.plugins.fault_injection_plugin import FaultInjectionPlugin

        fault_plugin = FaultInjectionPlugin.from_dict(faults_config)
        plugins.append((fault_plugin, "fault_injection", 25))
        logger.info("[emulation] Fault injection active: %s", list(faults_config.keys()))

    return plugins


def reset_emulation_plugins(plugins: List[Tuple[Any, str, int]]) -> None:
    """Reset per-run state on all emulation plugins.

    Called by the benchmark runner between runs to ensure ephemeral state
    is discarded and fault counters are reset.
    """
    for plugin, name, _ in plugins:  # noqa: B007
        if hasattr(plugin, "reset"):
            plugin.reset()
