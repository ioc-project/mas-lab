#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Runtime instantiation — ctl applies external state; runtime receives snapshots only."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mas.ctl.adapters.checkpoint import (
    InMemoryCheckpointStore,
    JsonCheckpointStore,
    build_checkpoint_store,
)
from mas.ctl.adapters.memory_seed import (
    MemorySeed,
    MemorySeedLoader,
    apply_memory_seeds,
    seeds_from_manifest,
)
from mas.ctl.compose.models import ResolvedInfra
from mas.ctl.infra.resolve import resolution_anchor
from mas.ctl.session.engine_factory import EngineSelection, build_engine
from mas.ctl.validate import validate_file, validation_enabled
from mas.ctl.workspace.config import WorkspaceConfig
from mas.library.standard.plugins.checkpoint import (
    HybridCheckpointStore as LibraryHybridCheckpointStore,
)
from mas.library.standard.plugins.checkpoint import (
    InMemoryCheckpointStore as LibraryInMemoryCheckpointStore,
)
from mas.library.standard.plugins.checkpoint import (
    JsonCheckpointStore as LibraryJsonCheckpointStore,
)
from mas.runtime.agent_defaults import default_pattern_plugin_id
from mas.runtime.boundary.context.manifest_context import context_chunks_from_spec
from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.driver.mocks import AutoCtxAssembler
from mas.runtime.engine.tools import is_control_tools_enabled, is_spawn_subagent_enabled, spawn_subagent_params
from mas.runtime.spec.checkpoint import parse_checkpoint_policy

logger = logging.getLogger(__name__)

_SKILL_TOOL_REFS = {
    "skills:tools/skill-access.tool.yaml",
    "pkg://skills/tools/skill-access.tool.yaml",
}
_SKILL_SHELL_REFS = {
    "skills:tools/run-skill-script.tool.yaml",
    "pkg://skills/tools/run-skill-script.tool.yaml",
}


def _supported_skill_impls() -> set[str]:
    """Discover accepted skill implementation names from PluginRegistry."""
    from mas.runtime.registry import get_registry

    return set(get_registry().list_names("skill_impl"))


def _overlay_providers_from_manifest(
    manifest: dict | None,
    resolved_infra: ResolvedInfra | None = None,
) -> list[Any]:
    """Instantiate providers from infra dependencies.

    MCP ``usage: use`` servers are client providers; ``usage: deploy`` entries
    are endpoints for the server command and are not connected here. An
    explicit local ToolServerRegistry claim re-adds the local provider beside MCP.
    """
    from mas.runtime.registry.tool_provider_registry import providers_from_manifest

    servers = (resolved_infra.tool_server_registry if resolved_infra else None) or {}
    return providers_from_manifest(manifest, tool_servers=servers)


@dataclass(frozen=True)
class _SkillPluginConfig:
    impl: str = "native"
    base_dir: Path | None = None
    auto_inject_scripts: bool = False
    pin_activated: bool = True
    allow_unload: bool = False
    auto_load: bool = False


@dataclass
class InstantiationOptions:
    pattern_plugin_id: str = field(default_factory=default_pattern_plugin_id)
    memory_seed_path: Path | None = None
    checkpoint_path: Path | None = None
    checkpoint_dir: Path | None = None
    validate_manifests: bool = True
    agent_manifest: dict | None = None
    manifest_dir: Path | None = None
    app_root: Path | None = None
    resolved_infra: ResolvedInfra | None = None
    workspace: WorkspaceConfig | None = None
    enable_observability: bool = True
    enable_governance: bool = True
    enable_coordination: bool = True
    hitl_contract: object | None = None
    user_io_contract: object | None = None
    cache_read_override: bool | None = None
    cache_write_override: bool | None = None
    cache_read_only: bool = False
    stream_override: bool | None = None
    engine: Any | None = None
    runtime_refs_cli: tuple[str, ...] = ()
    events_file: str | None = None
    model_override: str | None = None
    parent_default_model: str | None = None
    experiment_default_model: str | None = None
    experiment_model_slots: dict[str, str] | None = None
    parent_spec: dict | None = None
    extra_plugins: tuple[str, ...] = ()


def _lab_enable_plugins(start: Path | None) -> list[str]:
    """``lab.enable_plugins`` from the enclosing lab-config, if mas-lab is installed."""
    if start is None:
        return []
    try:
        from mas.lab.lab.config.lab_context import discover_lab_context
    except ImportError:
        return []
    return list(discover_lab_context(start).enable_plugins)


def hitl_contract_for_mode(mode: str) -> object | None:
    """``auto``: unattended run, answer HITL at once; ``block``: default registry wait."""
    if mode != "auto":
        return None
    from mas.runtime.registry import get_registry

    return get_registry().instantiate_by_type("hitl_contract", "auto_resolve")


def instantiate_runtime(
    options: InstantiationOptions,
    *,
    hitl=None,
) -> tuple[
    RuntimeInstance,
    JsonCheckpointStore | InMemoryCheckpointStore | LibraryHybridCheckpointStore | None,
]:
    """Ctl-owned bootstrap: validate seeds/checkpoints, build instance, restore state."""
    seeds: list[MemorySeed] = []
    if options.memory_seed_path:
        if options.validate_manifests and validation_enabled():
            validate_file(options.memory_seed_path, kind="memory_seed").raise_if_failed()
        seeds = MemorySeedLoader.load_path(options.memory_seed_path)
    if options.agent_manifest:
        seen = {s.key for s in seeds}
        for seed in seeds_from_manifest(options.agent_manifest):
            if seed.key not in seen:
                seeds.append(seed)
                seen.add(seed.key)

    ws = options.workspace or WorkspaceConfig.load(options.manifest_dir)
    if options.agent_manifest is not None:
        from mas.runtime.workspace_plugins import apply_workspace_plugins

        apply_workspace_plugins(
            options.agent_manifest,
            [
                *ws.plugins,
                *options.extra_plugins,
                *_lab_enable_plugins(options.manifest_dir),
            ],
        )
    policy = parse_checkpoint_policy(
        ((options.agent_manifest or {}).get("spec") or {}).get("checkpoint")
    )
    store = build_checkpoint_store(policy, options.checkpoint_dir)
    if store is None and policy.resolved_storage() == "memory":
        store = InMemoryCheckpointStore()
    if store and seeds:
        store.memory_seeds = [{"key": s.key, "content": s.content} for s in seeds]

    ctx = AutoCtxAssembler(pattern_plugin_id=options.pattern_plugin_id)
    ctx.checkpoint_portability = policy.portability
    _bind_archive_artefact(ctx, options)
    # Resolve local resource paths relative to the app root, not the process CWD.
    skill_base = options.manifest_dir or options.app_root or Path.cwd()
    tool_servers = (options.resolved_infra.tool_server_registry if options.resolved_infra else {}) or {}
    local_source = next(
        (server for server in tool_servers.values() if str(server.get("protocol") or "").lower() == "local"),
        {},
    )
    tools_dir = _resolve_local_resource_dir(local_source.get("tools_dir", "tools"), skill_base)
    skills_dir = _resolve_local_resource_dir(local_source.get("skills_dir", "skills"), skill_base)
    skill_cfg = _resolve_skill_plugin_config(
        options.agent_manifest,
        default_base_dir=skills_dir,
        relative_base_dir=skill_base,
    )
    _apply_manifest_context(
        ctx,
        options.agent_manifest,
        options.manifest_dir,
        app_root=options.app_root,
    )
    if options.agent_manifest and skill_base:
        from mas.library.skills.plugins.sk_catalog import attach_skill_catalog_plugin

        attach_skill_catalog_plugin(
            ctx,
            options.agent_manifest,
            skill_cfg.base_dir or skill_base,
            impl=skill_cfg.impl,
            auto_load=skill_cfg.auto_load,
            pin_activated=skill_cfg.pin_activated,
            allow_unload=skill_cfg.allow_unload,
        )
        ctx.skill_pin_activated = skill_cfg.pin_activated
        ctx.skill_allow_unload = skill_cfg.allow_unload
    ctx.capture_baseline()
    spec = dict((options.agent_manifest or {}).get("spec") or {})
    spawn_params = spawn_subagent_params(spec)
    ctx.allow_subagent_spawning = is_spawn_subagent_enabled(spec)
    ctx.allow_control_tools = is_control_tools_enabled(spec)
    ctx.subagent_templates = list((spawn_params or {}).get("templates") or [])
    # Keep agent spec isolated from MAS workflow policy. We only surface agency
    # participants (read-only) when available for context/routing helpers.
    if "agency" not in spec:
        mas_cfg = None
        composed_app = getattr(getattr(options, "bind", None), "composed_application", None)
        if composed_app is not None:
            mas_cfg = getattr(composed_app, "config", None)
        # Fallback source when available through agent_manifest wrappers.
        if mas_cfg is None and options.agent_manifest and isinstance(options.agent_manifest.get("mas"), dict):
            mas_cfg = options.agent_manifest.get("mas")

        if isinstance(mas_cfg, dict):
            mas_spec = mas_cfg.get("spec") if isinstance(mas_cfg.get("spec"), dict) else mas_cfg
            agency = mas_spec.get("agency") if isinstance(mas_spec, dict) else None
            if isinstance(agency, dict) and agency.get("agents"):
                spec["agency"] = {"agents": list(agency.get("agents") or [])}
    # Pre-parse spec to derive kernel config once; pass to build_engine to avoid double-parsing.
    from mas.runtime.spec.parser import parse_agent_spec

    _runtime_engine = (
        dict(options.resolved_infra.runtime_engine)
        if options.resolved_infra and options.resolved_infra.runtime_engine
        else None
    )
    _kernel_cfg, _obs_binding = parse_agent_spec(
        spec,
        runtime_engine=_runtime_engine,
        manifest_dir=options.manifest_dir,
    )
    if options.engine is not None:
        selection = EngineSelection(
            engine=options.engine,
            mode="injected",
            reason="InstantiationOptions.engine",
        )
    else:
        selection = build_engine(
            ctx,
            options.agent_manifest,
            options.resolved_infra,
            pattern_plugin_id=options.pattern_plugin_id,
            workspace_default_model=ws.default_model,
            anchor=resolution_anchor(options.manifest_dir, ws),
            workspace=ws,
            kernel_config=_kernel_cfg,
            cache_read_override=options.cache_read_override,
            cache_write_override=options.cache_write_override,
            cache_read_only=options.cache_read_only,
            stream_override=options.stream_override,
            runtime_refs_cli=list(options.runtime_refs_cli),
            model_override=options.model_override,
            parent_default_model=options.parent_default_model,
            experiment_default_model=options.experiment_default_model,
        )
    engine_obj = selection.engine
    if engine_obj is not None:
        engine_obj.model_slots = options.experiment_model_slots or {}
        engine_obj.parent_spec = options.parent_spec
    logger.debug("Engine mode=%s (%s)", selection.mode, selection.reason)

    from mas.library.standard.lib.context.compaction import apply_working_memory_compaction

    apply_working_memory_compaction(spec, engine=selection.engine)
    if "context_manager" in spec and options.agent_manifest is not None:
        # working_memory.compaction → context_manager is applied to a shallow
        # copy of spec; keep the engine's live manifest in sync. The engine
        # itself is bound onto ctx at driver init, not written into spec params.
        options.agent_manifest.setdefault("spec", {})["context_manager"] = spec["context_manager"]

    from mas.ctl.session.manifest_config import require_agent_id_from_manifest, resolve_mas_id

    agent_id = require_agent_id_from_manifest(options.agent_manifest)
    mas_id = resolve_mas_id(options.agent_manifest)
    instance = RuntimeInstance.from_spec(
        spec,
        base_dir=options.manifest_dir,
        agent_id=agent_id,
        mas_id=mas_id,
        hitl=hitl,
        engine=selection.engine,
        ctx=ctx,
        enable_observability=options.enable_observability,
        enable_governance=options.enable_governance,
        enable_coordination=options.enable_coordination,
        runtime_engine=_runtime_engine,
    )
    working_memory_spec = spec.get("working_memory")
    if isinstance(working_memory_spec, dict):
        from mas.runtime.boundary.context.working_memory_registry import WorkingMemoryConfig

        instance.working_memory = WorkingMemoryConfig(persistent=bool(working_memory_spec.get("persistent", True)))
    apply_memory_seeds(instance, seeds)
    if seeds and options.agent_manifest:
        from mas.ctl.adapters.memory_seed import index_seeds_in_semantic_memory

        index_seeds_in_semantic_memory(seeds, agent_id=agent_id)

    if options.checkpoint_path:
        path = Path(options.checkpoint_path)
        if isinstance(store, (LibraryJsonCheckpointStore, LibraryHybridCheckpointStore)):
            reader = store
        elif isinstance(store, LibraryInMemoryCheckpointStore) and path in store.snapshots:
            reader = store
        else:
            reader = JsonCheckpointStore(path.parent)
        payload = reader.load_payload(path)
        instance.load_checkpoint(payload["kernel"])
        if policy.resolved_storage() == "memory":
            store = InMemoryCheckpointStore()
        else:
            store = reader
        if getattr(reader, "memory_seeds", None):
            apply_memory_seeds(
                instance,
                [MemorySeed(key=r["key"], content=r["content"]) for r in reader.memory_seeds],
            )

    instance.capture_session_baseline()
    # Attach tools even in CLI-only mode (manifest_dir may be None for --tool flags)
    if options.agent_manifest:
        from mas.runtime.engine.manifest_tool_provider import attach_manifest_tools_to_instance

        hitl_contract = _resolve_interface_contract(
            options.hitl_contract,
            options.agent_manifest,
            "hitl_contract",
        )
        user_io_contract = _resolve_interface_contract(
            options.user_io_contract,
            options.agent_manifest,
            "user_io_contract",
        )
        attach_manifest_tools_to_instance(
            instance,
            options.agent_manifest,
            options.manifest_dir or Path.cwd(),
            app_root=options.app_root or options.manifest_dir,
            workspace_root=ws.root if ws.found else None,
            hitl_contract=hitl_contract,
            user_io_contract=user_io_contract,
            auto_inject_scripts=skill_cfg.auto_inject_scripts,
            tools_dir=tools_dir,
            skills_dir=skill_cfg.base_dir or skills_dir,
            overlay_providers=_overlay_providers_from_manifest(
                options.agent_manifest,
                options.resolved_infra,
            ),
        )
    return instance, store


def resolve_archive_dir(options: InstantiationOptions) -> str:
    """On-disk archive directory, or empty.

    Persist is off unless ``mas_ctl.archive.enabled`` is true. A path, an
    events file, or a checkpoint directory never turns it on — scaled
    experiments must not fill the disk by accident. ``paths.archive_dir``
    only chooses the location after enable.
    """
    from mas.runtime.spec.schema_defaults_generated import ARCHIVE_DIR_NAME, ARCHIVE_ENABLED

    ws = options.workspace
    archive_cfg = ws.mas_ctl.get("archive") if ws is not None else {}
    if not isinstance(archive_cfg, dict):
        archive_cfg = {}
    if not archive_cfg.get("enabled", ARCHIVE_ENABLED):
        return ""
    explicit = str((ws.paths.get("archive_dir") if ws is not None else "") or "").strip()
    dir_name = str(archive_cfg.get("dir_name") or ARCHIVE_DIR_NAME)
    if explicit and Path(explicit).is_absolute():
        return explicit
    events_file = str(options.events_file or "").strip()
    if events_file:
        events_path = Path(events_file).expanduser()
        if not events_path.is_absolute():
            root = options.app_root or options.manifest_dir or Path.cwd()
            events_path = root / events_path
        folder = events_path.parent if events_path.suffix else events_path
        name = Path(explicit).name if explicit else dir_name
        return str(folder / name)
    if explicit:
        root = options.app_root or options.manifest_dir or Path.cwd()
        return str(root / explicit)
    return ""


def _bind_archive_artefact(ctx: AutoCtxAssembler, options: InstantiationOptions) -> None:
    """Archive path/watermark come from config.yaml / run home, never agent spec."""
    ws = options.workspace
    watermark = None
    if ws is not None:
        archive_cfg = ws.mas_ctl.get("archive")
        if isinstance(archive_cfg, dict) and archive_cfg.get("memory_watermark_bytes") is not None:
            watermark = int(archive_cfg["memory_watermark_bytes"])
    ctx.archive_dir = resolve_archive_dir(options)
    if watermark is not None:
        ctx.archive_watermark_bytes = watermark
    if policy_needs_deep_fallback(ctx):
        ctx.checkpoint_portability = "self_contained"


def policy_needs_deep_fallback(ctx: AutoCtxAssembler) -> bool:
    """Shallow checkpoints require an on-disk archive."""
    return ctx.checkpoint_portability == "reference" and not ctx.archive_dir


def _resolve_interface_contract(explicit: object | None, manifest: dict | None, spec_key: str) -> object:
    """Resolve a manifest contract binding unless the caller supplied an instance."""
    if explicit is not None:
        return explicit
    from mas.runtime.registry import get_registry

    try:
        return get_registry().create(spec_key, manifest=manifest)
    except KeyError as exc:
        raise ValueError(f"unable to resolve spec.{spec_key}: {exc}") from exc


def _apply_manifest_context(
    ctx: AutoCtxAssembler,
    manifest: dict | None,
    manifest_dir: Path | None,
    *,
    app_root: Path | None = None,
) -> None:
    if not manifest:
        return
    spec = manifest.get("spec") or {}
    base = app_root or manifest_dir or Path.cwd()
    ctx.injected_context.extend(context_chunks_from_spec(spec, base_dir=base))


def _resolve_local_resource_dir(value: Any, app_root: Path) -> Path:
    path = Path(str(value or ".")).expanduser()
    return path.resolve() if path.is_absolute() else (app_root / path).resolve()


def _resolve_skill_plugin_config(
    manifest: dict[str, Any] | None,
    *,
    default_base_dir: Path,
    relative_base_dir: Path | None = None,
) -> _SkillPluginConfig:
    """Resolve skill plugin implementation from overlay/manifest/env settings.

    Priority: spec.context_sources (plugin list, same shape as observability)
    > legacy manifest tool/context declarations > env var > default.
    """
    env_impl = str(os.getenv("MAS_SKILL_IMPL") or "").strip().lower() or "native"
    supported_impls = _supported_skill_impls()
    impl = env_impl if env_impl in supported_impls else "native"
    rel_base: str | None = None
    auto_inject_scripts = False
    pin_activated = True
    allow_unload = False
    auto_load = False

    spec = manifest.get("spec") if isinstance(manifest, dict) and isinstance(manifest.get("spec"), dict) else {}
    context_sources_raw = spec.get("context_sources") if isinstance(spec, dict) else None
    if context_sources_raw:
        from mas.runtime.spec.context_sources import parse_context_sources

        plugins, configs = parse_context_sources(context_sources_raw)
        for candidate_impl in plugins:
            if candidate_impl in supported_impls:
                impl = candidate_impl
            else:
                logger.warning(
                    "Unknown context_sources plugin %r; expected one of %s",
                    candidate_impl,
                    sorted(supported_impls),
                )
        for cfg in configs.values():
            candidate_base = cfg.get("base_dir")
            if isinstance(candidate_base, str) and candidate_base.strip():
                rel_base = candidate_base
            if "auto_inject" in cfg:
                auto_inject_scripts = bool(cfg.get("auto_inject"))
            if "pin_activated" in cfg:
                pin_activated = bool(cfg.get("pin_activated"))
            if "allow_unload" in cfg:
                allow_unload = bool(cfg.get("allow_unload"))
            if "auto_load" in cfg:
                auto_load = bool(cfg.get("auto_load"))

    for entry in _iter_skill_plugin_entries(manifest):
        candidate_impl = _entry_skill_impl(entry)
        if candidate_impl:
            normalized = candidate_impl.strip().lower()
            if normalized in supported_impls:
                impl = normalized
            else:
                logger.warning(
                    "Unknown skill impl %r; expected one of %s",
                    candidate_impl,
                    sorted(supported_impls),
                )
        candidate_base = _entry_skill_base_dir(entry)
        if candidate_base:
            rel_base = candidate_base

    resolved_base = default_base_dir
    if rel_base:
        p = Path(rel_base)
        resolved_base = p.resolve() if p.is_absolute() else ((relative_base_dir or default_base_dir) / p).resolve()

    return _SkillPluginConfig(
        impl=impl,
        base_dir=resolved_base,
        auto_inject_scripts=auto_inject_scripts,
        pin_activated=pin_activated,
        allow_unload=allow_unload,
        auto_load=auto_load,
    )


def _iter_skill_plugin_entries(manifest: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not manifest:
        return []

    entries: list[dict[str, Any]] = []
    spec = manifest.get("spec") if isinstance(manifest.get("spec"), dict) else {}

    for tools_block in (spec.get("tools"), manifest.get("tools")):
        if not isinstance(tools_block, list):
            continue
        for item in tools_block:
            if not isinstance(item, dict):
                continue
            ref = str(item.get("ref") or "").strip()
            if ref in _SKILL_TOOL_REFS or ref in _SKILL_SHELL_REFS:
                entries.append(item)

    return entries


def _entry_skill_impl(entry: dict[str, Any]) -> str | None:
    impl = entry.get("impl")
    if isinstance(impl, str) and impl.strip():
        return impl
    params = entry.get("params")
    if isinstance(params, dict):
        p_impl = params.get("impl")
        if isinstance(p_impl, str) and p_impl.strip():
            return p_impl
    return None


def _entry_skill_base_dir(entry: dict[str, Any]) -> str | None:
    for key in ("base-dir", "base_dir"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value
    params = entry.get("params")
    if isinstance(params, dict):
        value = params.get("base_dir")
        if isinstance(value, str) and value.strip():
            return value
    return None


def _auto_inject_skill_tools(manifest: dict[str, Any] | None, *, auto_inject_scripts: bool = False) -> None:
    """Deprecated no-op.

    Skill tools are system tools now: listing ``spec.skills`` injects
    ``activate_skill``; a skill with ``scripts/`` (or an explicit
    ``kind: system`` / ``auto_inject`` opt-in) injects ``run_skill_script``.
    Kept so older tests/callers do not break.
    """
    _ = (manifest, auto_inject_scripts)
