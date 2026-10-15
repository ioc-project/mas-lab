#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Prepare output directory and scenario configurations."""

import json as _json
import logging
import os
import shutil as _shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from mas.lab.benchmark.run_manager import BenchmarkRunManager
from mas.lab.benchmark.schedule.metadata import register_mas_run
from mas.lab.benchmark.schedule.run_batch.load import LoadedExperiment
from mas.runtime.package_refs import path_ref_for_anchor
from mas.runtime.spec.infra_paths import experiment_infra_bundle_path

logger = logging.getLogger(__name__)


def extra_overlays_for_scenario(scenario_id: str) -> list[str]:
    """Optional extra overlays for a scenario. Stock engine has none."""
    del scenario_id
    return []


def _resolve_run_infra_refs(loaded: LoadedExperiment) -> list[str]:
    """Resolve CLI ``--infra`` bundle refs for benchmark MAS runs.

    Infra refs are never read from agent/MAS/overlays or from experiment YAML;
    they merge at run time like ``mas-ctl``: workspace ``config.yaml``,
    ``MAS_INFRA_REFS``, user default, then CLI. This helper only prepends the
    experiment-local bundle named by ``--infra`` (e.g. ``gls-vllm`` →
    ``<experiment_dir>/infra/gls-vllm.yaml``) so it participates in that merge
    via ``RunContext.infra_refs``.
    """
    refs: list[str] = []
    name = loaded.infra_name
    if not name:
        return refs
    bundle = experiment_infra_bundle_path(loaded.experiment_yaml.parent, name)
    if bundle is None:
        logger.warning(
            "infra bundle '%s' not found under %s/infra",
            name,
            loaded.experiment_yaml.parent,
        )
        return refs
    mas_root = loaded.experiment_yaml.parent
    mas = getattr(loaded.exp, "mas", None)
    manifest = getattr(mas, "manifest", None) if mas is not None else None
    if manifest is not None:
        mas_root = Path(manifest).resolve().parent
    ref = path_ref_for_anchor(bundle, mas_root)
    return [ref]


@dataclass
class PreparedBatch:
    """Runtime state after output and scenario preparation."""

    output_dir: Path
    csv_path: Path
    mas_meta: Any
    loaded_ids: list[str]
    scenario_configs: dict
    scenario_overlay_stacks: dict
    scenario_flavours: dict
    mas_app: str = ""
    mas_app_version: str = ""
    mas_ref: str = ""
    scenario_overlay_refs: dict = field(default_factory=dict)
    overlays_dir: Optional[Path] = None
    overlay_base_dir: Optional[Path] = None
    infra_refs: list[str] = field(default_factory=list)
    dataset_items: list = field(default_factory=list)


def setup_output_dir(
    loaded: LoadedExperiment,
    *,
    output_dir: Optional[Path] = None,
    force: bool = False,
) -> tuple[Path, Path, Any]:
    """Create output directory, register metadata, optionally force-clean."""
    exp = loaded.exp
    if output_dir is not None:
        output_dir = output_dir.expanduser().resolve()
    else:
        output_dir = exp.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "results.csv"

    _mas_meta = register_mas_run(output_dir, loaded.experiment_yaml, exp)
    _mas_run_manager = BenchmarkRunManager()
    _mas_run_manager.record_last_run(
        _mas_meta, output_dir, experiment_yaml=loaded.experiment_yaml,
    )
    logger.info("Benchmark ID: %s  (%s)", _mas_meta.short_id, output_dir)

    if force:
        for _child in output_dir.iterdir():
            if _child.name in ("metadata.yaml", ".cache"):
                continue
            if _child.is_dir():
                _shutil.rmtree(_child)
            else:
                _child.unlink()
        logger.info("--force: cleaned %s", output_dir)

    return output_dir, csv_path, _mas_meta


def preload_scenario_configs(
    loaded: LoadedExperiment,
    *,
    infra_refs: list[str] | None = None,
) -> tuple[dict, dict, list[str]]:
    """Load scenario configs once per scenario."""
    from mas.lab.lab.config import load_stacked_config, load_scenario_config

    exp = loaded.exp
    configs_dir = loaded.configs_dir
    experiment_yaml = loaded.experiment_yaml
    scenario_ids = loaded.scenario_ids
    infra_refs = list(infra_refs or [])

    _scenario_configs: dict = {}
    _scenario_overlay_stacks: dict = {}
    for _sid in scenario_ids:
        try:
            _scenario_spec = exp.get_scenario(_sid)
            declared = list(_scenario_spec.overlays.flattened()) if _scenario_spec else []
            overlays = []
            seen: set[str] = set()
            for item in [*declared, *extra_overlays_for_scenario(_sid)]:
                marker = _json.dumps(item, sort_keys=True) if isinstance(item, dict) else str(item)
                if marker in seen:
                    continue
                seen.add(marker)
                overlays.append(item)
            if overlays and exp.mas and exp.mas.manifest:
                _cfg, _bp = load_stacked_config(
                    exp.mas.manifest, overlays,
                    overlays_dir=configs_dir,
                    base_dir=experiment_yaml.parent,
                    infra_refs=infra_refs,
                    workspace_root=experiment_yaml.parent,
                )
                _scenario_overlay_stacks[_sid] = overlays
            elif (configs_dir is None or not overlays) and exp.mas and exp.mas.manifest:
                from mas.lab.manifest.load import load_mas_config
                _mas_man = load_mas_config(
                    exp.mas.manifest,
                    validate=False,
                    infra_refs=infra_refs,
                    workspace_root=experiment_yaml.parent,
                )
                _cfg, _bp = dict(_mas_man._raw), exp.mas.manifest
            else:
                _explicit_mas = exp.mas.manifest if (exp.mas and exp.mas.manifest) else None
                _cfg, _bp = load_scenario_config(
                    configs_dir,
                    _sid,
                    mas_yaml=_explicit_mas,
                    infra_refs=infra_refs,
                    workspace_root=experiment_yaml.parent,
                )
            _scenario_configs[_sid] = (_cfg, _bp)
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"scenario {_sid!r}: cannot load its configuration ({exc}). Declare "
                "scenarios[].overlays explicitly (use empty lists for none)."
            ) from exc

    return _scenario_configs, _scenario_overlay_stacks, list(scenario_ids)


def resolve_scenario_flavours(
    loaded: LoadedExperiment,
    loaded_ids: list[str],
    flavour_name: str,
) -> dict:
    """Resolve per-scenario flavour overrides."""
    exp = loaded.exp
    experiment_yaml = loaded.experiment_yaml
    _scenario_flavours: dict = {}
    for _sid in loaded_ids:
        _sc_spec_fl = exp.get_scenario(_sid)
        _sc_flavour_name = getattr(_sc_spec_fl, "flavour", None) if _sc_spec_fl else None
        if _sc_flavour_name and _sc_flavour_name != flavour_name:
            try:
                from mas.lab.flavour.load import load_flavour as _load_flavour
                _fl_filename = f"{_sc_flavour_name}.yaml"
                _fl_candidates = [
                    experiment_yaml.parent / "flavours" / _fl_filename,
                ]
                _fl_path = next((p for p in _fl_candidates if p.exists()), None)
                if _fl_path:
                    _scenario_flavours[_sid] = _load_flavour(_fl_path)
                    logger.info(f"Scenario '{_sid}' using flavour: {_sc_flavour_name} ({_fl_path})")
                else:
                    logger.warning(f"Scenario '{_sid}' flavour '{_sc_flavour_name}' not found")
            except Exception as _fe:
                logger.warning(f"Failed to load scenario flavour '{_sc_flavour_name}': {_fe}")
    return _scenario_flavours


def extract_mas_provenance(loaded: LoadedExperiment) -> tuple[str, str, str]:
    """Extract MAS app provenance from manifest."""
    from mas.lab.paths import workspace_relative_path
    from mas.lab.workspace import find_workspace_root

    exp = loaded.exp
    ws = find_workspace_root(loaded.experiment_yaml.parent)
    _mas_app = ""
    _mas_app_version = ""
    _mas_ref = ""
    if exp.mas and exp.mas.manifest and Path(exp.mas.manifest).exists():
        try:
            from mas.runtime.spec.source import load_yaml_file

            _mas_doc = load_yaml_file(Path(exp.mas.manifest))
            _mas_meta_doc = _mas_doc.get("metadata", {})
            _mas_app = str(_mas_meta_doc.get("name", ""))
            _mas_app_version = str(_mas_meta_doc.get("version", ""))
            _mas_ref = workspace_relative_path(exp.mas.manifest, start=ws or loaded.experiment_yaml.parent)
        except Exception:
            logger.debug('suppressed', exc_info=True)
    return _mas_app, _mas_app_version, _mas_ref


def _overlay_ref_for_entry(
    entry: Any,
    *,
    configs_dir: Path | None,
    experiment_yaml: Path,
) -> str:
    if isinstance(entry, dict) and "ref" in entry:
        return str((experiment_yaml.parent / entry["ref"]).resolve())
    if isinstance(entry, str):
        if configs_dir:
            return str((configs_dir / f"{entry}.yaml").resolve())
        return entry
    return ""


def build_scenario_overlay_refs(
    loaded: LoadedExperiment,
    loaded_ids: list[str],
) -> dict:
    """Build per-scenario overlay refs for run_info."""
    from mas.lab.paths import workspace_relative_path
    from mas.lab.workspace import find_workspace_root

    exp = loaded.exp
    configs_dir = loaded.configs_dir
    experiment_yaml = loaded.experiment_yaml
    ws = find_workspace_root(experiment_yaml.parent)
    _scenario_overlay_refs: dict = {}
    for _sid in loaded_ids:
        _sc_spec_ov = exp.get_scenario(_sid)
        _stack = getattr(_sc_spec_ov, "overlays", None) if _sc_spec_ov else None
        _flat = _stack.flattened() if _stack is not None else []
        if _flat:
            _resolved = _overlay_ref_for_entry(
                _flat[0],
                configs_dir=configs_dir,
                experiment_yaml=experiment_yaml,
            )
            _scenario_overlay_refs[_sid] = workspace_relative_path(
                _resolved, start=ws or experiment_yaml.parent
            )
        elif configs_dir:
            _ov_path = configs_dir / f"{_sid}.yaml"
            _scenario_overlay_refs[_sid] = (
                workspace_relative_path(_ov_path, start=ws or experiment_yaml.parent)
                if _ov_path.exists()
                else ""
            )
    return _scenario_overlay_refs


async def prepare_batch(
    loaded: LoadedExperiment,
    *,
    output_dir: Optional[Path] = None,
    force: bool = False,
    data_cache_dir: Optional[Path] = None,
    clean_stale: Optional[bool] = None,
) -> PreparedBatch:
    """Set up output, preload scenarios, run pre-pipeline phase."""
    from mas.lab.benchmark.schedule.pipeline import run_pipeline_phase
    from mas.lab.benchmark.stale_cleanup import maybe_handle_stale_outputs

    output_dir, csv_path, mas_meta = setup_output_dir(loaded, output_dir=output_dir, force=force)
    if not force:
        maybe_handle_stale_outputs(
            output_dir,
            loaded.scenario_ids,
            loaded.experiment_yaml,
            clean_stale=clean_stale,
            trace_cache_dir=loaded.trace_cache_dir,
        )
    infra_refs = _resolve_run_infra_refs(loaded)
    scenario_configs, scenario_overlay_stacks, loaded_ids = preload_scenario_configs(
        loaded, infra_refs=infra_refs
    )
    scenario_flavours = resolve_scenario_flavours(loaded, loaded_ids, loaded.flavour_name)
    mas_app, mas_app_version, mas_ref = extract_mas_provenance(loaded)
    scenario_overlay_refs = build_scenario_overlay_refs(loaded, loaded_ids)

    dataset_items = list(loaded.dataset_items)
    _pre_dataset = await run_pipeline_phase(
        phase="pre",
        exp=loaded.exp,
        experiment_yaml=loaded.experiment_yaml,
        output_dir=output_dir,
        specs=loaded.pipeline_specs,
        scenario_ids=loaded_ids,
        infra_name=loaded.infra_name,
        step_overrides=loaded.step_overrides_dict,
        data_cache_dir=data_cache_dir,
        # Only experiment-level ``pre`` runs here, once, before any run.
        # scenario / item / run ``pre`` run right before the runs beneath them
        # (see ``pre_hooks``); expanding them over run folders found on disk
        # produced no instances on a clean directory and stale ones on a rerun.
        scopes={"experiment"},
    )
    if _pre_dataset is not None:
        dataset_items = _pre_dataset

    return PreparedBatch(
        output_dir=output_dir,
        csv_path=csv_path,
        mas_meta=mas_meta,
        loaded_ids=loaded_ids,
        scenario_configs=scenario_configs,
        scenario_overlay_stacks=scenario_overlay_stacks,
        scenario_flavours=scenario_flavours,
        mas_app=mas_app,
        mas_app_version=mas_app_version,
        mas_ref=mas_ref,
        scenario_overlay_refs=scenario_overlay_refs,
        overlays_dir=loaded.configs_dir,
        overlay_base_dir=loaded.experiment_yaml.parent,
        infra_refs=infra_refs,
        dataset_items=dataset_items,
    )
