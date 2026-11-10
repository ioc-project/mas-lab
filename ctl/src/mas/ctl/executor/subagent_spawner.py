#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""One-shot, bounded subagent materialization for workflow engine tools."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml
from mas.ctl.executor.engine_tool_context import MaterializedEngineToolContext
from mas.ctl.executor.spawn_ledger import SpawnLedger
from mas.ctl.manifest.spec_bindings import SpecBindingError, parse_subagent_templates
from mas.library.standard.plugins.tools.containment import containment_roots, resolve_under_roots
from mas.runtime.boundary.context.working_memory_registry import WorkingMemoryRegistry
from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.engine.tools import SPAWN_SUBAGENT_TOOL, spawn_subagent_params
from mas.runtime.registry import get_registry


def _resolve_engine_tool_class(name: str) -> type:
    """Resolve an ``engine_tool_provider`` variant. No plugin class is named here."""
    variant = get_registry().resolve_by_type("engine_tool_provider", name)
    if variant is None:
        raise KeyError(f"engine_tool_provider {name!r} is not registered")
    return variant.load_class()


def subagents_state_from_engine(engine: Any) -> Any | None:
    """The bind state of the granted orchestration plugin on this engine, if any."""
    if engine is None:
        return None
    from mas.runtime.engine.leaf import leaf_engine

    leaf = leaf_engine(engine)
    provider = getattr(leaf, "tool_provider", None)
    getter = getattr(provider, "system_plugins", None)
    bound = getter() if callable(getter) else None
    state = bound.state_for_capability("orchestration") if bound is not None else None
    return state if isinstance(getattr(state, "ledger", None), SpawnLedger) else None


def ledger_from_engine(engine: Any) -> SpawnLedger | None:
    """The spawn ledger the subagents system plugin bound on this engine, if any."""
    state = subagents_state_from_engine(engine)
    return state.ledger if state is not None else None


@dataclass(frozen=True)
class SubagentTemplate:
    """Validated local agent manifest selected by a spawning tool call."""

    template_id: str
    description: str
    path: Path
    manifest: dict[str, Any]


def load_subagent_templates(
    manifest: dict[str, Any],
    manifest_dir: Path | None,
) -> dict[str, SubagentTemplate]:
    """Resolve and validate template refs within the manifest's allowed roots."""
    raw_templates = (spawn_subagent_params(manifest.get("spec")) or {}).get("templates")
    try:
        rows = parse_subagent_templates(raw_templates)
    except SpecBindingError as exc:
        raise ValueError(str(exc)) from exc
    if not rows:
        return {}
    if manifest_dir is None:
        raise ValueError("manifest_dir is required when spawn_subagent templates are declared")

    from mas.ctl.manifest.spec_bindings import validate_agent_spec_bindings
    from mas.ctl.validate import validate_file

    base_dir = Path(manifest_dir).resolve()
    roots = containment_roots(base_dir, base_dir)
    templates: dict[str, SubagentTemplate] = {}
    for row in rows:
        template_id = row["id"]
        path = resolve_under_roots(base_dir, row["ref"], containment_roots=roots)
        if not path.is_file():
            raise FileNotFoundError(f"subagent template {template_id!r} not found: {path}")
        template_manifest = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(template_manifest, dict):
            raise ValueError(f"subagent template {template_id!r} must be a manifest object")
        validate_file(path, kind="agent").raise_if_failed()
        validate_agent_spec_bindings(template_manifest.get("spec") or {})
        templates[template_id] = SubagentTemplate(
            template_id=template_id,
            description=row.get("description") or "",
            path=path,
            manifest=template_manifest,
        )
    return templates


def make_subagent_spawner(
    *,
    materialized: Any = None,
    parent_agent_id: str,
    session_id: str,
    templates: dict[str, SubagentTemplate],
    ledger: SpawnLedger,
    working_memory_registry: WorkingMemoryRegistry,
    display: Any = None,
    verbose: int = 0,
    instance_factory: Callable[[SubagentTemplate, str], RuntimeInstance] | None = None,
    controller_factory: Callable[..., Any] | None = None,
    context: Any = None,
    flavour_spec: dict[str, Any] | None = None,
    parent_spec: dict[str, Any] | None = None,
    entry_id: str = "",
    delegate_ids: tuple[str, ...] = (),
) -> Any:
    """Build EngineToolContext, then instantiate the registered spawn plugin."""
    templates = dict(templates)
    if context is None:
        context = MaterializedEngineToolContext(
            materialized=materialized,
            session_id=session_id,
            parent_agent_id=parent_agent_id,
            ledger=ledger,
            working_memory_registry=working_memory_registry,
            display=display,
            verbose=verbose,
            instance_factory=_adapt_instance_factory(templates, instance_factory),
            controller_factory=controller_factory,
            flavour_spec=flavour_spec,
            parent_spec=parent_spec,
            entry_id=entry_id,
            delegate_ids=delegate_ids,
        )
    return _resolve_engine_tool_class(SPAWN_SUBAGENT_TOOL)(
        parent_agent_id=parent_agent_id,
        session_id=session_id,
        templates=templates,
        context=context,
    )


def _adapt_instance_factory(
    templates: dict[str, SubagentTemplate],
    legacy: Callable[[SubagentTemplate, str], RuntimeInstance] | None,
) -> Callable[..., RuntimeInstance] | None:
    """Keep the template-shaped factory seam the spawner's own tests use."""
    if legacy is None:
        return None

    def factory(_manifest: Any, _manifest_dir: Any, child_id: str, template_id: str) -> RuntimeInstance:
        return legacy(templates[template_id], child_id)

    return factory


def wire_subagent_spawning(
    engine: Any,
    *,
    materialized: Any,
    manifest: dict[str, Any],
    manifest_dir: Path | None,
    parent_agent_id: str,
    session_id: str,
    working_memory_registry: WorkingMemoryRegistry | None = None,
    ledger: SpawnLedger | None = None,
    display: Any = None,
    verbose: int = 0,
    instance_factory: Callable[[SubagentTemplate, str], RuntimeInstance] | None = None,
    controller_factory: Callable[..., Any] | None = None,
    flavour_spec: dict[str, Any] | None = None,
    parent_spec: dict[str, Any] | None = None,
    entry_id: str = "",
    delegate_ids: tuple[str, ...] = (),
) -> Any | None:
    """Attach the spawner contract only when the tools entry declares it."""
    spec = manifest.get("spec") or {}
    params = spawn_subagent_params(spec)
    if params is None:
        return None
    templates = load_subagent_templates(manifest, manifest_dir)
    if not templates:
        raise ValueError("spawn_subagent requires at least one entry in params.templates")

    if working_memory_registry is None:
        working_memory_registry = getattr(materialized, "_session_working_memory_registry", None)
        if working_memory_registry is None:
            working_memory_registry = WorkingMemoryRegistry()
            materialized._session_working_memory_registry = working_memory_registry
    state = subagents_state_from_engine(engine)
    if ledger is None and state is not None:
        ledger = state.ledger
    if ledger is None:
        ledger = getattr(materialized, "_subagent_spawn_ledger", None)
    if ledger is None:
        ledger = SpawnLedger(
            max_depth=params.get("max_depth", 3),
            max_spawns=params.get("max_spawns", 8),
        )
    if materialized is not None:
        materialized._subagent_spawn_ledger = ledger

    from mas.runtime.engine.leaf import leaf_engine

    leaf = leaf_engine(engine)
    if state is not None:
        # the granted subagents plugin owns the call: hand it the spawner, add no second contract
        existing = state.spawner
        if existing is not None and getattr(existing, "parent_agent_id", None) == parent_agent_id:
            return existing
        state.ledger = ledger
    else:
        for existing in getattr(leaf, "engine_tool_contracts", ()) or ():
            is_spawn = getattr(existing, "is_subagent_tool", None)
            if (
                callable(is_spawn)
                and is_spawn(SPAWN_SUBAGENT_TOOL)
                and getattr(existing, "parent_agent_id", None) == parent_agent_id
            ):
                return existing
    spawner = make_subagent_spawner(
        materialized=materialized,
        parent_agent_id=parent_agent_id,
        session_id=session_id,
        templates=templates,
        ledger=ledger,
        working_memory_registry=working_memory_registry,
        display=display,
        verbose=verbose,
        instance_factory=instance_factory,
        controller_factory=controller_factory,
        flavour_spec=flavour_spec,
        parent_spec=parent_spec,
        entry_id=entry_id,
        delegate_ids=delegate_ids,
    )
    if state is not None:
        state.spawner = spawner
    else:  # a host with no manifest tool provider cannot dispatch plugin tools: keep the engine contract
        contracts = list(getattr(leaf, "engine_tool_contracts", ()) or ())
        contracts.append(spawner)
        leaf.engine_tool_contracts = tuple(contracts)
    return spawner
