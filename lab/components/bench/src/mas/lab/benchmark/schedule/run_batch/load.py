#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

"""Load and validate a MAS experiment for batch execution."""

from __future__ import annotations

import logging
import sys as _sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

from mas.lab.benchmark.execution import parse_step_overrides
from mas.lab.benchmark.execution.pipeline_attach import merge_pipeline_attachments
from mas.lab.benchmark.schedule.pipeline_resolve import resolve_pipeline_specs

logger = logging.getLogger(__name__)
_ItemT = TypeVar("_ItemT")


@dataclass
class LoadedExperiment:
    """Configuration resolved from experiment YAML before execution."""

    exp: Any
    experiment_yaml: Path
    configs_dir: Optional[Path]
    scenario_ids: list[str]
    dataset_items: list
    pipeline_specs: list
    step_overrides_dict: dict
    flavour: Any
    flavour_name: str
    infra_name: Optional[str]
    n_runs: int
    trace_cache_dir: Optional[Path]


def load_experiment(
    experiment_yaml: Path,
    *,
    overrides: Optional[list[str]] = None,
    max_runs: Optional[int] = None,
    limit_scenarios: Optional[int] = None,
    scenario_id: Optional[str] = None,
    dataset_item: Optional[str] = None,
    single_run: bool = False,
    flavour_name: Optional[str] = None,
    infra_name: Optional[str] = None,
    trace_cache_dir: Optional[Path] = None,
    step_overrides: Optional[list] = None,
    pipeline_attachments: Optional[list] = None,
    experiment_overlays: Optional[list] = None,
) -> LoadedExperiment | None:
    """Load MASExperimentConfig and resolve scenarios, dataset, flavour."""
    try:
        from mas.lab.lab.config import MASExperimentConfig
    except ImportError as exc:
        logger.error(f"Cannot import MASExperimentConfig: {exc}")
        return None

    try:
        from mas.lab.manifests.validator import (
            ManifestValidationError,
            validate_manifest,
        )
        from mas.runtime.spec.source import load_yaml_file

        raw = load_yaml_file(experiment_yaml)

        if overrides:
            from mas.ctl.overrides import apply_cli_overrides

            raw = apply_cli_overrides(raw, tuple(overrides), root="experiment")

        if experiment_overlays:
            from mas.lab.benchmark.execution.experiment_overlay import (
                apply_experiment_overlays,
            )
            raw = apply_experiment_overlays(
                raw,
                [Path(p) for p in experiment_overlays],
                base_dir=experiment_yaml.parent,
            )

        if pipeline_attachments:
            raw = merge_pipeline_attachments(raw, pipeline_attachments)

        validate_manifest(
            raw,
            source=str(experiment_yaml),
            kind="experiment",
            base_dir=experiment_yaml.parent,
        )
    except ManifestValidationError as exc:
        logger.error("Experiment manifest validation failed: %s", exc)
        return None
    except Exception as exc:
        logger.error("Experiment manifest validation error: %s", exc)
        return None

    logger.info(f"Loading MAS experiment: {experiment_yaml}")
    try:
        from mas.ctl.env import env_model_override

        exp = MASExperimentConfig.from_data(
            raw, experiment_yaml, model_override=env_model_override()
        )
    except Exception as exc:
        logger.error(f"Failed to load MAS experiment: {exc}")
        return None

    from mas.lab.lab.config import discover_lab_context, inject_lab_libraries

    inject_lab_libraries(discover_lab_context(experiment_yaml))

    _lab_dir = str(experiment_yaml.parent)
    if _lab_dir not in _sys.path:
        _sys.path.insert(0, _lab_dir)

    try:
        from mas.lab.workspace import WorkspaceConfig as _WsCfg
        _ws_libs = _WsCfg.load(experiment_yaml.parent)
        if _ws_libs.found:
            for _lib_rel in (_ws_libs._data.get("libraries") or []):
                _lib_abs = str((_ws_libs._path / _lib_rel).resolve())
                if _lib_abs not in _sys.path:
                    _sys.path.append(_lib_abs)
                    logger.debug("Library path added to sys.path: %s", _lib_abs)
    except Exception:
        logger.debug('suppressed', exc_info=True)

    _step_overrides_dict = parse_step_overrides(step_overrides)
    if _step_overrides_dict:
        logger.info("CLI step overrides: %s", _step_overrides_dict)

    logger.info(f"MAS Experiment: {exp.name}")
    logger.info(f"  Description: {exp.description}")

    trace_cache_dir = trace_cache_dir or getattr(exp, "trace_cache_dir", None)

    if not flavour_name and exp.default_flavour:
        flavour_name = exp.default_flavour
        logger.info(f"Using default flavour from experiment: {flavour_name}")
    if not flavour_name:
        flavour_name = "local"

    _flavour = None
    if flavour_name:
        try:
            from mas.lab.flavour.load import load_flavour as _load_flavour
            from mas.lab.flavour.resolve import resolve_flavour_path

            _fpath = resolve_flavour_path(flavour_name)
            _flavour = _load_flavour(_fpath)
            logger.info(
                "Flavour: %s (mas-library-standard: %s)",
                _flavour.get("metadata", {}).get("name", flavour_name),
                _fpath,
            )
        except FileNotFoundError as _fe:
            logger.error("%s", _fe)
            return None
        except Exception as _fe:
            logger.warning("Failed to load flavour '%s': %s", flavour_name, _fe)

    if not infra_name:
        infra_name = getattr(exp, "default_infra", None) or None
    if infra_name:
        logger.info("Using infra bundle: %s", infra_name)

    configs_dir: Optional[Path]
    if exp.mas and exp.mas.configs_dir:
        configs_dir = exp.mas.configs_dir
    elif exp.mas and exp.mas.manifest:
        configs_dir = exp.mas.manifest.parent / "overlays"
    else:
        configs_dir = experiment_yaml.parent / "overlays"

    scenario_ids = exp.scenario_ids()
    if not scenario_ids:
        if not configs_dir.is_dir():
            logger.error(f"configs_dir not found: {configs_dir}")
            return None
        from mas.lab.lab.config import discover_scenario_stems
        scenario_ids = discover_scenario_stems(configs_dir)
        if not scenario_ids:
            logger.error(f"No scenarios found under {configs_dir}")
            return None
    elif not configs_dir.is_dir():
        logger.debug(f"configs_dir not found: {configs_dir} — no overlay files (scenarios explicitly declared)")
        configs_dir = None

    _pipeline_specs = resolve_pipeline_specs(exp, experiment_yaml)
    dataset_items = _view_dataset_items(
        _load_dataset_items(exp),
        dataset_filter=exp.dataset_filter,
        dataset_limit=exp.dataset_limit,
    )
    try:
        scenario_ids = _select_scenario_ids(scenario_ids, scenario_id)
        dataset_items = _select_dataset_items(dataset_items, dataset_item)
    except ValueError as exc:
        logger.error("%s", exc)
        return None

    dataset_items = _cross_checkpoint_axis(
        dataset_items,
        exp.checkpoints,
        explicit=exp.checkpoints_explicit,
    )

    n_runs = max_runs if max_runs is not None else getattr(exp, "n_runs", None)
    if n_runs is None:
        n_runs = exp.execution.n_runs if exp.execution else 1
    if single_run:
        limit_scenarios = 1
        n_runs = 1
        dataset_items = dataset_items[:1]
    if limit_scenarios is not None:
        scenario_ids = scenario_ids[:limit_scenarios]

    total = len(scenario_ids) * len(dataset_items) * n_runs
    logger.info(f"  Scenarios : {len(scenario_ids)}, items: {len(dataset_items)}, runs: {n_runs} = {total} total")

    return LoadedExperiment(
        exp=exp,
        experiment_yaml=experiment_yaml,
        configs_dir=configs_dir,
        scenario_ids=scenario_ids,
        dataset_items=dataset_items,
        pipeline_specs=_pipeline_specs,
        step_overrides_dict=_step_overrides_dict,
        flavour=_flavour,
        flavour_name=flavour_name,
        infra_name=infra_name,
        n_runs=n_runs,
        trace_cache_dir=trace_cache_dir,
    )


def _view_dataset_items(
    items: list,
    *,
    dataset_filter: dict | None = None,
    dataset_limit: int | None = None,
) -> list:
    """Filter then cap — cap is first N of the filtered list, not a reduced Dataset file."""
    viewed = list(items)
    if dataset_filter:
        before = len(viewed)
        viewed = [
            item for item in viewed
            if all(item.get(k) == v for k, v in dataset_filter.items())
        ]
        logger.info("Dataset filter %s: %s → %s items", dataset_filter, before, len(viewed))
    if dataset_limit is not None and len(viewed) > dataset_limit:
        viewed = viewed[:dataset_limit]
        logger.info("Dataset limit: capped to %s items", dataset_limit)
    return viewed


def _select_scenario_ids(scenario_ids: list[str], scenario_id: str | None) -> list[str]:
    return _select_by_id(
        scenario_ids,
        scenario_id,
        label="Scenario",
        id_of=lambda value: value,
    )


def _select_dataset_items(items: list[dict], dataset_item: str | None) -> list[dict]:
    return _select_by_id(
        items,
        dataset_item,
        label="Dataset item",
        id_of=lambda item: item.get("id"),
    )


def _cross_checkpoint_axis(items: list[dict], checkpoints: list[Any], *, explicit: bool) -> list[dict]:
    """Cross a declared checkpoint axis without changing legacy item identities."""
    if not explicit or (len(checkpoints) == 1 and checkpoints[0].id == "none"):
        return items

    expanded: list[dict] = []
    for item in items:
        original_id = str(item.get("id", "item"))
        original_inputs = dict(item.get("inputs") or {})
        item_checkpoint = original_inputs.get("checkpoint")
        if item_checkpoint:
            logger.warning(
                "experiment.checkpoints axis overrides inputs.checkpoint for dataset item %s",
                original_id,
            )
        for checkpoint in checkpoints:
            expanded_item = dict(item)
            inputs = dict(original_inputs)
            checkpoint_settings = dict(inputs.get("checkpoint") or {})
            if checkpoint.id == "none":
                checkpoint_settings.pop("load", None)
            else:
                checkpoint_settings["load"] = str(checkpoint.path)
            if checkpoint_settings:
                inputs["checkpoint"] = checkpoint_settings
            else:
                inputs.pop("checkpoint", None)
            expanded_item["inputs"] = inputs
            expanded_item["source_item_id"] = original_id
            expanded_item["checkpoint_id"] = "" if checkpoint.id == "none" else checkpoint.id
            expanded_item["forked_from_checkpoint"] = str(checkpoint.path) if checkpoint.path else ""
            expanded_item["id"] = f"{original_id}--checkpoint-{checkpoint.id}"
            expanded.append(expanded_item)
    return expanded


def _select_by_id(
    items: list[_ItemT],
    selected_id: str | None,
    *,
    label: str,
    id_of: Callable[[_ItemT], object],
) -> list[_ItemT]:
    """Return the requested ID while keeping selector behavior consistent."""
    if selected_id is None:
        return items
    selected = [item for item in items if str(id_of(item)) == str(selected_id)]
    if not selected:
        raise ValueError(f"{label} ID not found: {selected_id}")
    return selected


def _load_dataset_items(exp: Any) -> list:
    if not exp.dataset:
        raise ValueError("experiment declares no dataset; set experiment.dataset.path")
    if not Path(exp.dataset).exists():
        raise FileNotFoundError(f"experiment dataset not found: {exp.dataset}")
    from mas.lab.benchmark.dataset import Dataset

    ds = Dataset.from_yaml(
        Path(exp.dataset),
        source_overlay=getattr(exp, "dataset_source", None),
    )
    dataset_items = [item.to_dict() for item in ds]
    if not dataset_items:
        raise ValueError(f"experiment dataset has no items: {exp.dataset}")
    logger.info(f"Dataset: {exp.dataset} ({len(dataset_items)} items)")
    return dataset_items


def print_dry_run(loaded: LoadedExperiment) -> None:
    """Print dry-run summary to stdout."""
    exp = loaded.exp
    total = len(loaded.scenario_ids) * len(loaded.dataset_items) * loaded.n_runs
    print()
    print("=" * 70)
    print("DRY RUN — MAS Benchmark Configuration")
    print("=" * 70)
    print(f"Experiment : {exp.name}")
    if exp.mas and exp.mas.manifest:
        print(f"Manifest   : {exp.mas.manifest}")
    print(f"configs_dir: {loaded.configs_dir}")
    print(f"Scenarios  : {loaded.scenario_ids}")
    print(f"Dataset    : {len(loaded.dataset_items)} items")
    print(f"Runs/item  : {loaded.n_runs}")
    print(f"Total      : {total} executions")
    _pipeline_specs = loaded.pipeline_specs
    if _pipeline_specs:
        _by_scope: dict = {}
        for s in _pipeline_specs:
            _by_scope.setdefault(s.scope, []).append(s)
        _parts = []
        for scope in ("run", "item", "scenario", "experiment"):
            count = len(_by_scope.get(scope, []))
            if count:
                _parts.append(f"{count} {scope}")
        extra = f" ({', '.join(_parts)})" if _parts else ""
        print(f"Pipeline   : {len(_pipeline_specs)} steps{extra}")
    print("=" * 70)
    print("\n✓ Configuration valid — ready to run")
