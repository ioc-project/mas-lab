#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""Load experiment pipeline steps into memory — inline, named ref, or app bundle.

After :func:`resolve_pipeline_specs`, execution is identical regardless of source.
Uses :mod:`mas.runtime.spec.source` for path / app resolution (same as other manifests).
A neighbouring file is not loaded unless the manifest or CLI ``--pipeline`` names it.
"""

import logging
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from mas.runtime.spec.source import resolve_app_resource, resolve_yaml_path

logger = logging.getLogger(__name__)


@runtime_checkable
class PipelineHost(Protocol):
    """Experiment object that can declare pipeline steps or a named ref."""

    def all_pipeline_steps(self) -> list: ...

    pipeline: Any
    pipeline_ref: str | None
    pipeline_app: dict | None


def resolve_pipeline_specs(exp: object, experiment_yaml: Path) -> list:
    """Return all pipeline step specs for an experiment (may be empty).

    Resolution order (first non-empty wins):

    1. Experiment manifest — ``all_pipeline_steps()`` (level hooks, including
       CLI ``--pipeline`` attachments merged into those hooks)
    2. External ref — ``pipeline_ref`` or ``pipeline: path/to.yaml`` (string)
    3. App bundle — ``pipeline_app`` (``{app: name, name: <file>}``)

    No filename is inferred.
    """
    specs = _specs_from_experiment(exp)
    if specs:
        return specs

    ref_path = _pipeline_ref_path(exp, experiment_yaml)
    if ref_path is not None:
        return _load_specs_from_yaml(ref_path)

    app_ref = _pipeline_app(exp)
    if isinstance(app_ref, dict) and app_ref.get("app"):
        try:
            return _load_specs_from_yaml(resolve_app_resource(app_ref))
        except FileNotFoundError as exc:
            logger.warning("%s", exc)
            return []

    return []


def resolve_pipeline_specs_from_yaml(path: Path) -> list:
    """Load step specs from a standalone pipeline YAML file."""
    return _load_specs_from_yaml(path)


def _specs_from_experiment(exp: object) -> list:
    if isinstance(exp, PipelineHost):
        steps = exp.all_pipeline_steps()
        if steps:
            return list(steps)
    elif callable(type(exp).__dict__.get("all_pipeline_steps")):
        steps = type(exp).all_pipeline_steps(exp)
        if steps:
            return list(steps)
    pipeline = _field(exp, "pipeline")
    if isinstance(pipeline, list) and pipeline:
        return list(pipeline)
    return []


def _pipeline_ref_path(exp: object, experiment_yaml: Path) -> Path | None:
    raw_ref = _field(exp, "pipeline_ref")
    if isinstance(raw_ref, str) and raw_ref.strip():
        return _resolve_existing_path(raw_ref.strip(), experiment_yaml.parent)
    raw = _field(exp, "pipeline")
    if isinstance(raw, str) and raw.strip():
        return _resolve_existing_path(raw.strip(), experiment_yaml.parent)
    return None


def _pipeline_app(exp: object) -> dict | None:
    raw = _field(exp, "pipeline_app")
    return raw if isinstance(raw, dict) else None


def _field(exp: object, name: str) -> Any:
    try:
        return exp.__getattribute__(name)
    except AttributeError:
        return None


def _resolve_existing_path(ref: str, anchor: Path) -> Path | None:
    try:
        path = resolve_yaml_path(ref, anchor)
    except FileNotFoundError:
        return None
    return path


def _load_specs_from_yaml(path: Path) -> list:
    from mas.lab.benchmark.pipeline import Pipeline
    from mas.lab.lab.config import PipelineStepSpec

    try:
        step_dicts = Pipeline.step_dicts_from_yaml(path)
    except Exception as exc:
        logger.warning("Failed to load pipeline from %s: %s", path, exc)
        return []

    return [PipelineStepSpec.from_dict(dict(step_data)) for step_data in step_dicts]


def spec_to_step_dict(spec: Any) -> dict:
    """Normalise a :class:`PipelineStepSpec` to executor step dict."""
    return {
        "name": spec.name or spec.type,
        "type": spec.type,
        "phase": spec.phase,
        "config": dict(spec.config or {}),
        "depends_on": list(spec.depends_on or []),
        "scope": spec.scope,
        "resource": spec.resource,
        "refresh": spec.refresh,
        "concurrency": int(spec.concurrency or 1),
    }
