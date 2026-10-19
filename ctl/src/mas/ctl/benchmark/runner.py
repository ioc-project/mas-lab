#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""MAS bench integration — default ctl runtime path for mas-lab benchmark."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from mas.ctl.adapters.memory_seed import MemorySeed, MemorySeedLoader, apply_memory_seeds
from mas.ctl.benchmark.runner_dispatch import mas_manifest_path
from mas.ctl.compose.models import ResolvedInfra
from mas.ctl.compose.runner import ComposeRequest, ComposeResult, compose_run
from mas.ctl.deployment.runtime_id import DEFAULT_RUNTIME_ID
from mas.ctl.paths import OverlayRefEntry, resolve_overlay_ref_entries
from mas.ctl.executor.mas_session import (
    agent_manifest_label,
    entry_agent_id,
    materialize_mas_compose,
    prepare_delegation_entry_session,
    resolve_entry_pattern_plugin_id,
    wire_peer_delegation,
)
from mas.ctl.executor.subagent_spawner import wire_subagent_spawning
from mas.ctl.infra.resolve import resolve_infra_refs
from mas.ctl.session.bootstrap import InstantiationOptions, hitl_contract_for_mode, instantiate_runtime
from mas.ctl.session.controller import ConversationConfig, SessionController, close_observability
from mas.ctl.workspace.config import UserConfig, WorkspaceConfig, merge_infra_refs
from mas.runtime.boundary.context.working_memory_registry import WorkingMemoryRegistry
from mas.lab.manifest.load import (
    agent_manifest_from_path,
    entry_agent_from_compose,
    is_loaded_mas_raw,
    merge_stacked_entry_agent_manifest,
    should_merge_stacked_entry_agent_config,
)
from mas.lab.runners.constants import DEFAULT_LAB_RUNNER_ID
from mas.lab.runners.protocol import RunResult
from mas.runtime.run_artifact import RunArtifact

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ControllerTarget:
    instance: Any
    store: Any
    manifest: dict[str, Any]
    manifest_path: Path
    topology: str | None = None
    obs_recorder: Any = None
    obs_pipeline: Any = None
    scoped_recorders: Any = ()
    session_id: str = ""
    working_memory_registry: Any = None


def _resolve_ref(ref: str | Path, anchor: Path) -> Path:
    p = Path(ref)
    return p if p.is_absolute() else (anchor / p).resolve()


def _attach_tool_fixtures(instances: Any, tool_fixtures: Any, params: dict[str, Any] | None = None) -> None:
    """Expose the run's dataset fixtures (``ctx.tool_fixtures``) and overlay params (``ctx.runtime_params``)."""
    for instance in instances:
        ctx = getattr(getattr(instance, "driver", None), "ctx", None)
        if ctx is None:
            continue
        if tool_fixtures is not None:
            ctx.tool_fixtures = tool_fixtures
        if params:
            ctx.runtime_params = dict(params)


def _checkpoint_path(checkpoint_load: Any, anchor: Path) -> Path | None:
    if checkpoint_load is None:
        return None
    if isinstance(checkpoint_load, str):
        return _resolve_ref(checkpoint_load, anchor)
    if isinstance(checkpoint_load, dict):
        path_ref = checkpoint_load.get("path") or checkpoint_load.get("ref")
        if path_ref:
            return _resolve_ref(str(path_ref), anchor)
    return None


def _memory_seeds_from_run_input(
    memory_seeds: list[dict[str, Any]] | None,
) -> list[MemorySeed]:
    if not memory_seeds:
        return []
    return MemorySeedLoader.load_data(memory_seeds)


def _manifest_kind_on_disk(spec_path: Path) -> str:
    from mas.runtime.spec.source import load_yaml_mapping

    try:
        doc = load_yaml_mapping(spec_path)
    except (FileNotFoundError, OSError):
        return ""
    return str((doc or {}).get("kind", "")).lower()


def _manifest_with_flavour_observability(
    manifest: dict[str, Any],
    flavour: Any,
) -> dict[str, Any]:
    """Overlay flavour ``spec.observability`` onto *manifest* for bench runs."""
    if not flavour or not isinstance(flavour, dict):
        return manifest
    fl_spec = flavour.get("spec")
    if not isinstance(fl_spec, dict):
        fl_spec = flavour
    fl_obs = fl_spec.get("observability")
    if not fl_obs:
        return manifest
    merged = dict(manifest)
    spec = dict(merged.get("spec") or {})
    spec["observability"] = list(fl_obs) if isinstance(fl_obs, list) else fl_obs
    merged["spec"] = spec
    return merged


def bench_obs_config(
    output_dir: Path,
    manifest: dict[str, Any],
    manifest_path: Path,
    *,
    mas_config: dict[str, Any] | None = None,
    flavour: Any = None,
) -> tuple[Path, Any]:
    """Return ``(events_path, obs_cfg)`` for one bench run output dir."""
    from mas.ctl.cli.obs_flags import resolve_observability_config
    from mas.ctl.session.manifest_config import mas_id_from_manifest

    output_dir.mkdir(parents=True, exist_ok=True)
    events_path = output_dir / "traces" / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    mas_id = mas_id_from_manifest(mas_config) if mas_config else mas_id_from_manifest(manifest)
    effective_manifest = _manifest_with_flavour_observability(manifest, flavour)
    obs_cfg = resolve_observability_config(
        events=True,
        events_file=str(events_path),
        events_stdout=False,
        events_format=None,
        agent_id=agent_manifest_label(manifest, manifest_path),
        mas_id=mas_id,
        manifest=effective_manifest,
    )
    return events_path, obs_cfg


def _events_artifacts(events_path: Path, manifest: dict[str, Any], manifest_path: Path) -> list[RunArtifact]:
    if events_path.is_file() and events_path.stat().st_size > 0:
        return [
            RunArtifact(
                kind="events",
                path=events_path,
                meta={"agent_id": agent_manifest_label(manifest, manifest_path)},
            )
        ]
    return []


def ensure_live_otel_span_files(events_path: Path, obs_cfg: Any) -> None:
    """Backfill otel/observe JSONL span exports from events.jsonl when missing."""
    import logging

    logger = logging.getLogger(__name__)
    plugins = list(getattr(obs_cfg, "plugins", None) or [])
    norm = {(p or "").split("@")[0].replace("-", "_") for p in plugins}
    if not norm.intersection({"otel", "observe_sdk", "ioa_observe"}):
        return
    if not events_path.is_file() or events_path.stat().st_size == 0:
        return

    traces_dir = events_path.parent
    targets: list[Path] = []
    if "otel" in norm:
        targets.append(traces_dir / "otel_sdk_spans.jsonl")
    if norm.intersection({"observe_sdk", "ioa_observe"}):
        targets.append(traces_dir / "observe_sdk_spans.jsonl")

    try:
        from mas.library.telemetry.conversion.exporter import OTEL_AVAILABLE
        from mas.library.telemetry.conversion.layers import parse_export_layers
        from mas.library.telemetry.conversion.replay import replay_events_file
    except ImportError:
        return
    if not OTEL_AVAILABLE:
        return

    otel_cfg = (obs_cfg.plugin_configs or {}).get("otel") or {}
    mas_name = str(obs_cfg.mas_id or "")
    layers = parse_export_layers(otel_cfg)

    def _span_line_count(path: Path) -> int:
        if not path.is_file() or path.stat().st_size == 0:
            return 0
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())

    for dest in targets:
        live_count = _span_line_count(dest)
        if live_count > 0:
            # Live plugin already wrote spans — don't overwrite.
            logger.debug("Span export already present (%d spans): %s", live_count, dest)
            continue
        try:
            replay_events_file(
                events_path,
                dest,
                app_name=mas_name,
                export_layers=layers,
            )
            logger.info("Backfilled span export: %s (%d spans)", dest, _span_line_count(dest))
        except Exception:
            logger.warning("Failed to materialize span export %s", dest, exc_info=True)


def _resolve_overlay_paths(
    overlay_refs: list[OverlayRefEntry],
    *,
    manifest_path: Path,
    overlays_dir: Path | None = None,
    base_dir: Path | None = None,
) -> list[Path]:
    if not overlay_refs:
        return []
    return list(
        resolve_overlay_ref_entries(
            overlay_refs,
            manifest_dir=manifest_path.parent,
            overlays_dir=overlays_dir,
            base_dir=base_dir,
        )
    )


class MasBenchRunner:
    """Execute agent prompts via v2 SessionController (bench harness integration)."""

    runner_id: str = DEFAULT_LAB_RUNNER_ID

    def run(
        self,
        prompt: str,
        *,
        config: dict[str, Any],
        spec_path: Path,
        output_dir: Path,
        run_input: Any = None,
        run_seed: int = 0,
        infra_refs: list[str] | None = None,
        mas_path: Path | None = None,
        overlay_refs: list[OverlayRefEntry] | None = None,
        overlays_dir: Path | None = None,
        overlay_base_dir: Path | None = None,
        flavour: Any = None,
        **kwargs: Any,
    ) -> RunResult:
        from mas.lab.inputs import RunInput

        ri: RunInput | None = run_input if isinstance(run_input, RunInput) else None

        queries = ri.scripted_queries() if ri else ([prompt] if prompt else [])
        if not queries:
            return RunResult(content="", status="error", error="no user messages in run input")

        memory_seeds = _memory_seeds_from_run_input(ri.memory_seeds if ri else None)
        checkpoint_load = ri.checkpoint_load if ri else None
        checkpoint_save = ri.checkpoint_save if ri else False
        tool_fixtures = ri.tool_fixtures if ri else None
        run_params = config.get("params") if isinstance(config.get("params"), dict) else None

        checkpoint_path = _checkpoint_path(checkpoint_load, spec_path.parent)
        checkpoint_dir = output_dir / "checkpoints" if checkpoint_save else None

        _infra = list(infra_refs or [])
        _overlay_refs: list[OverlayRefEntry] = list(overlay_refs or [])
        _overlays_dir = overlays_dir
        _overlay_base = overlay_base_dir

        experiment_default_model = kwargs.get("experiment_default_model")
        experiment_model_slots = kwargs.get("experiment_model_slots")
        if not experiment_default_model and isinstance(experiment_model_slots, dict):
            from mas.runtime.spec.model_ref import SLOT_MAIN, slot_model

            experiment_default_model = slot_model(experiment_model_slots, SLOT_MAIN)
        resolved = self._resolve_target(
            config=config,
            spec_path=spec_path,
            mas_path=mas_path,
            overlay_refs=_overlay_refs,
            overlays_dir=_overlays_dir,
            overlay_base_dir=_overlay_base,
            infra_refs=_infra,
            memory_seeds=memory_seeds,
            checkpoint_path=checkpoint_path,
            checkpoint_dir=checkpoint_dir,
            checkpoint_save=checkpoint_save,
            queries=queries,
            output_dir=output_dir,
            run_seed=run_seed,
            flavour=flavour,
            experiment_default_model=experiment_default_model,
            experiment_model_slots=experiment_model_slots if isinstance(experiment_model_slots, dict) else None,
            workspace_root=kwargs.get("workspace_root"),
            tool_fixtures=tool_fixtures,
            run_params=run_params,
        )
        if isinstance(resolved, RunResult):
            return self._with_bench_metadata(resolved, run_seed=run_seed)

        result = self._run_controller_turns(
            instance=resolved.instance,
            store=resolved.store,
            config=resolved.manifest,
            spec_path=resolved.manifest_path,
            output_dir=output_dir,
            queries=queries,
            memory_seeds=memory_seeds,
            checkpoint_save=checkpoint_save,
            checkpoint_path=checkpoint_path,
            run_seed=run_seed,
            topology=resolved.topology,
            session_id=resolved.session_id,
            working_memory_registry=resolved.working_memory_registry,
            obs_recorder=resolved.obs_recorder,
            obs_pipeline=resolved.obs_pipeline,
            scoped_recorders=resolved.scoped_recorders,
            flavour=flavour,
        )
        return self._with_bench_metadata(result, run_seed=run_seed)

    @staticmethod
    def _with_bench_metadata(result: RunResult, *, run_seed: int) -> RunResult:
        meta = dict(result.metadata)
        meta.setdefault("runtime_id", DEFAULT_RUNTIME_ID)
        meta.setdefault("run_seed", run_seed)
        meta.setdefault("adapter_plugin", "mas-lab-bench.plugins.mas")
        if result.metadata == meta:
            return result
        return RunResult(
            content=result.content,
            status=result.status,
            error=result.error,
            artifacts=result.artifacts,
            metadata=meta,
        )

    def _resolve_target(
        self,
        *,
        config: dict[str, Any],
        spec_path: Path,
        mas_path: Path | None,
        overlay_refs: list[OverlayRefEntry],
        overlays_dir: Path | None,
        overlay_base_dir: Path | None,
        infra_refs: list[str],
        memory_seeds: list[MemorySeed],
        checkpoint_path: Path | None,
        checkpoint_dir: Path | None,
        checkpoint_save: bool | str,
        queries: list[str],
        output_dir: Path,
        run_seed: int,
        flavour: Any = None,
        experiment_default_model: str | None = None,
        experiment_model_slots: dict[str, str] | None = None,
        workspace_root: Path | None = None,
        tool_fixtures: Any = None,
        run_params: dict[str, Any] | None = None,
    ) -> RunResult | _ControllerTarget:
        entry_manifest = config
        entry_manifest_path = spec_path

        resolved_mas_path = mas_path or mas_manifest_path(config, spec_path)
        if resolved_mas_path is None:
            has_oasf_doc = isinstance(config.get("metadata"), dict) and isinstance(
                config.get("spec"), dict
            )
            if _manifest_kind_on_disk(spec_path) == "agent" and (
                is_loaded_mas_raw(config) or not has_oasf_doc
            ):
                entry_manifest, entry_manifest_path = agent_manifest_from_path(
                    spec_path,
                    overlay_refs=overlay_refs,
                    overlays_dir=overlays_dir,
                    overlay_base_dir=overlay_base_dir,
                )
            elif str(config.get("kind", "")).lower() == "agent" and not has_oasf_doc:
                entry_manifest, entry_manifest_path = agent_manifest_from_path(
                    spec_path,
                    overlay_refs=overlay_refs,
                    overlays_dir=overlays_dir,
                    overlay_base_dir=overlay_base_dir,
                )
            if should_merge_stacked_entry_agent_config(config):
                entry_manifest = merge_stacked_entry_agent_manifest(entry_manifest, config)
            return self._standalone_controller_target(
                entry_manifest=entry_manifest,
                entry_manifest_path=entry_manifest_path,
                infra_refs=infra_refs,
                checkpoint_path=checkpoint_path,
                checkpoint_dir=checkpoint_dir,
                experiment_default_model=experiment_default_model,
                experiment_model_slots=experiment_model_slots,
                workspace_root=workspace_root,
                tool_fixtures=tool_fixtures,
                run_params=run_params,
            )

        # Do not prepend observability-native. That overlay is target.kind:
        # Agent; compose now fans it onto every agency row, which would give
        # each agent a private native sink on top of bench_obs_config's
        # shared traces/events.jsonl. The harness already instruments MAS
        # runs via setup_run_observability.
        overlay_paths = _resolve_overlay_paths(
            overlay_refs,
            manifest_path=resolved_mas_path,
            overlays_dir=overlays_dir,
            base_dir=overlay_base_dir,
        )
        compose = compose_run(
            ComposeRequest(
                manifest=resolved_mas_path,
                workspace_root=workspace_root,
                overlay_paths=overlay_paths,
                infra_refs=infra_refs,
                validate=False,
            )
        )
        entry_manifest, entry_manifest_path = entry_agent_from_compose(compose, resolved_mas_path)
        if should_merge_stacked_entry_agent_config(config):
            entry_manifest = merge_stacked_entry_agent_manifest(entry_manifest, config)

        bind = compose.bind
        bind.experiment_default_model = experiment_default_model
        bind.experiment_model_slots = experiment_model_slots
        # Bench runs are unattended: nobody can answer request_human_input.
        bind.hitl_mode = "auto"
        entry = entry_agent_id(compose.mas_config)

        if len(bind.agents) <= 1:
            pattern_plugin_id = resolve_entry_pattern_plugin_id(
                compose, entry, entry_manifest=entry_manifest
            )
            instance, store = instantiate_runtime(
                InstantiationOptions(
                    agent_manifest=entry_manifest,
                    manifest_dir=entry_manifest_path.parent,
                    resolved_infra=compose.resolved_infra or ResolvedInfra(),
                    workspace=WorkspaceConfig.load(workspace_root or resolved_mas_path.parent),
                    validate_manifests=False,
                    checkpoint_path=checkpoint_path,
                    checkpoint_dir=checkpoint_dir,
                    pattern_plugin_id=pattern_plugin_id,
                    experiment_default_model=experiment_default_model,
                    experiment_model_slots=experiment_model_slots,
                    hitl_contract=hitl_contract_for_mode("auto"),
                ),
            )
            _attach_tool_fixtures([instance], tool_fixtures, run_params)
            return _ControllerTarget(instance, store, entry_manifest, entry_manifest_path)

        materialized = materialize_mas_compose(compose, mas_base_dir=resolved_mas_path.parent)

        try:
            events_path, obs_cfg = bench_obs_config(
                output_dir,
                entry_manifest,
                entry_manifest_path,
                mas_config=compose.mas_config,
                flavour=flavour,
            )
            try:
                prepared = prepare_delegation_entry_session(
                    materialized,
                    entry_id=entry,
                    entry_manifest=entry_manifest,
                    entry_manifest_path=entry_manifest_path,
                    display=None,
                    verbose=0,
                )
                # Wire delegation onto every OTHER agent that declares its
                # own peers too (not just the entry) — see
                # wire_peer_delegation's docstring: without this, an agent
                # that is itself a delegate can never further delegate.
                wire_peer_delegation(
                    materialized,
                    entry_id=entry,
                    display=None,
                    verbose=0,
                    already_wired={entry},
                    session_id=prepared.session_id,
                    working_memory_registry=getattr(prepared, "working_memory_registry", None),
                )
            except KeyError:
                raise
        except KeyError as exc:
            logger.error("Run failed: %s", exc, exc_info=True)
            return RunResult(content="", status="error", error=str(exc))

        store = self._checkpoint_store(checkpoint_dir, checkpoint_path)
        if checkpoint_path is not None and checkpoint_path.is_file() and store is not None:
            prepared.instance.load_checkpoint(store.load(checkpoint_path))

        # Wire every materialized agent (moderator + specialists), not just the
        # entry instance, onto one shared events sink — otherwise delegated
        # sub-agent turns (make_workflow_send) run with no observability
        # attached at all and never reach events.jsonl (see run_mas.py's
        # setup_run_observability, which this mirrors).
        from mas.ctl.adapters.obs.session import SessionObservabilityRecorder
        from mas.ctl.session.observability import setup_run_observability

        instances = dict(materialized.materialized.instances)
        _attach_tool_fixtures(instances.values(), tool_fixtures, run_params)
        shared_pipeline, scoped_recorders = setup_run_observability(
            instances, obs_cfg, base_dir=output_dir, entry_agent_id=entry,
        )
        obs_rec = None
        if shared_pipeline is not None:
            obs_rec = SessionObservabilityRecorder(plugin_set=shared_pipeline, owns_plugin_set=False)

        return _ControllerTarget(
            prepared.instance,
            store,
            prepared.enriched_manifest,
            prepared.manifest_path,
            topology="delegation",
            obs_recorder=obs_rec,
            obs_pipeline=shared_pipeline,
            scoped_recorders=scoped_recorders,
            session_id=prepared.session_id,
            working_memory_registry=getattr(prepared, "working_memory_registry", None),
        )

    def _standalone_controller_target(
        self,
        *,
        entry_manifest: dict[str, Any],
        entry_manifest_path: Path,
        infra_refs: list[str],
        checkpoint_path: Path | None,
        checkpoint_dir: Path | None,
        experiment_default_model: str | None = None,
        experiment_model_slots: dict[str, str] | None = None,
        workspace_root: Path | None = None,
        tool_fixtures: Any = None,
        run_params: dict[str, Any] | None = None,
    ) -> _ControllerTarget:
        workspace = WorkspaceConfig.load(workspace_root or entry_manifest_path.parent)
        user = UserConfig.load()
        merged = merge_infra_refs(
            workspace_refs=workspace.effective_infra_refs,
            user_refs=[user.default_infra] if user.default_infra else [],
            cli_refs=list(infra_refs or []),
            workspace_found=workspace.found,
        )
        resolved = resolve_infra_refs(
            merged, anchor=entry_manifest_path.parent, workspace=workspace, user=user
        )
        entry_id = agent_manifest_label(entry_manifest, entry_manifest_path)
        pattern_plugin_id = resolve_entry_pattern_plugin_id(
            None, entry_id, entry_manifest=entry_manifest
        )
        instance, store = instantiate_runtime(
            InstantiationOptions(
                agent_manifest=entry_manifest,
                manifest_dir=entry_manifest_path.parent,
                resolved_infra=resolved,
                workspace=workspace,
                validate_manifests=False,
                checkpoint_path=checkpoint_path,
                checkpoint_dir=checkpoint_dir,
                pattern_plugin_id=pattern_plugin_id,
                experiment_default_model=experiment_default_model,
                experiment_model_slots=experiment_model_slots,
                hitl_contract=hitl_contract_for_mode("auto"),
            ),
        )
        _attach_tool_fixtures([instance], tool_fixtures, run_params)
        session_id = str(uuid.uuid4())
        working_memory_registry = WorkingMemoryRegistry()
        # Without this the tool is advertised but has no contract behind it.
        wire_subagent_spawning(
            getattr(getattr(instance, "driver", None), "engine", None),
            materialized=SimpleNamespace(instances={entry_id: instance}, bus=None),
            manifest=entry_manifest,
            manifest_dir=entry_manifest_path.parent,
            parent_agent_id=entry_id,
            session_id=session_id,
            working_memory_registry=working_memory_registry,
        )
        return _ControllerTarget(
            instance,
            store,
            entry_manifest,
            entry_manifest_path,
            session_id=session_id,
            working_memory_registry=working_memory_registry,
        )

    @staticmethod
    def _checkpoint_store(
        checkpoint_dir: Path | None,
        checkpoint_path: Path | None,
    ):
        from mas.ctl.adapters.checkpoint import JsonCheckpointStore

        if checkpoint_dir is not None:
            return JsonCheckpointStore(checkpoint_dir)
        if checkpoint_path is not None:
            return JsonCheckpointStore(checkpoint_path.parent)
        return None

    def _run_controller_turns(
        self,
        *,
        instance: Any,
        store: Any,
        config: dict[str, Any],
        spec_path: Path,
        output_dir: Path,
        queries: list[str],
        memory_seeds: list[MemorySeed],
        checkpoint_save: bool | str,
        checkpoint_path: Path | None,
        run_seed: int,
        topology: str | None = None,
        session_id: str = "",
        working_memory_registry: Any = None,
        obs_recorder: Any = None,
        obs_pipeline: Any = None,
        scoped_recorders: Any = (),
        flavour: Any = None,
    ) -> RunResult:
        from mas.ctl.session.observability import setup_observability
        from mas.ctl.ui.stdout import StdoutConversationDisplay

        events_path, obs_cfg = bench_obs_config(
            output_dir, config, spec_path, flavour=flavour
        )
        obs_rec = obs_recorder if obs_recorder is not None else setup_observability(
            instance, obs_cfg, base_dir=output_dir
        )

        if memory_seeds:
            apply_memory_seeds(instance, memory_seeds)

        display = StdoutConversationDisplay(show_labels=False, verbose=0)
        controller = SessionController(
            instance=instance,
            display=display,
            obs_recorder=obs_rec,
            checkpoint_store=store,
            agent_id=agent_manifest_label(config, spec_path),
            config=ConversationConfig(
                single_turn=len(queries) == 1,
                save_checkpoint_each_turn=bool(checkpoint_save),
            ),
            session_id=session_id,
            working_memory_registry=working_memory_registry,
        )
        from mas.ctl.session.manager import SessionManager

        session_manager = SessionManager(checkpoint_store=store)
        managed_session = session_manager.create(
            instance,
            controller,
            config,
            session_id=session_id or None,
            working_memory_registry=working_memory_registry,
        )
        if checkpoint_path is not None and store is not None:
            session_manager.restore_checkpoint(
                managed_session,
                checkpoint_path,
                manifest_content=config,
            )
        controller.managed_session = managed_session
        try:
            results = [controller.run_turn(q) for q in queries]
        finally:
            close_observability(controller)
            for recorder in scoped_recorders:
                recorder.close()
            if obs_pipeline is not None:
                obs_pipeline.close()
            ensure_live_otel_span_files(events_path, obs_cfg)

        if checkpoint_save and store is not None:
            label = checkpoint_save if isinstance(checkpoint_save, str) else "final"
            final_path = session_manager.checkpoint(managed_session.session_id, label=label)
            logger.info("Saved final checkpoint: %s", final_path)

        text = results[-1].text if results else ""

        meta: dict[str, Any] = {"run_seed": run_seed, "turns": len(queries)}
        if topology:
            meta["topology"] = topology

        return RunResult(
            content=text,
            status="ok",
            artifacts=_events_artifacts(events_path, config, spec_path),
            metadata={**meta, "session_id": managed_session.session_id},
        )


def select_mas_runner(*, runtime_id: str | None = None) -> MasBenchRunner:
    """Return the default MAS bench runner.

    ``runtime_id`` is reserved for future multi-runtime selection; ignored today.
    """
    del runtime_id
    return MasBenchRunner()
