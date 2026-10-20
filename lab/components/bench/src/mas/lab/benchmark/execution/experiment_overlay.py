#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Experiment overlay — apply one or more overlay YAMLs to a raw experiment dict.

An experiment overlay is a YAML file that amends an ``experiment.yaml`` without
modifying it.  It uses the same merge semantics as agent/MAS overlays (RFC 7386 merge patch):
mappings merge recursively, while lists, including
pipeline hook lists (``run.post``, ``item.post``, ``post:``, …), are *replaced*.
An overlay that sets a hook list must therefore carry the full desired list.

Overlay format
--------------
::

    apiVersion: mas/v1
    kind: ExperimentOverlay
    metadata:
      name: my-overlay           # optional
    spec:
      # Any key valid inside experiment: {}
      schedule:
        parallel_scenarios: 8
      bench_emulation:
        runtime:
          cache: content-addressed
      run:
        post:
          - ref: pipelines/my-eval.yaml   # replaces base run.post list
      post:
        - ref: pipelines/my-plots.yaml  # replaces experiment-level post:

Precedence (lowest → highest)
------------------------------
experiment.yaml  <  --override  <  overlay files (in order)  <  pipeline attachments
(--infra, --flavour, --max-runs and --set are applied afterwards)

CLI usage
---------
::

    mas-lab benchmark run experiment.yaml \\
        -x schedule/local-dev.yaml \\
        -x pipelines/standard-eval-overlay.yaml

Multiple ``-x`` flags are applied in order (left to right).
"""

from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

from mas.ctl.overlay import apply_merge_patch

logger = logging.getLogger(__name__)


def _apply_single_overlay(
    experiment_data: Dict[str, Any],
    overlay_data: Dict[str, Any],
    overlay_path: Path,
) -> Dict[str, Any]:
    """Apply one overlay dict to the raw experiment dict.

    Uses RFC 7386 JSON Merge Patch semantics via :func:`apply_merge_patch`:
    dict + dict → recursive merge, everything else (including lists) → replace.

    *experiment_data* is the raw ``{experiment: {...}}`` dict.
    *overlay_data* is the parsed overlay YAML.
    """
    # Normalise to {experiment: {...}} wrapper
    exp_block = experiment_data.get("experiment", experiment_data)

    # Extract spec from overlay — support both bare dict and {spec: {...}} form
    overlay_spec: Dict[str, Any]
    if "spec" in overlay_data:
        overlay_spec = overlay_data["spec"]
    elif "experiment" in overlay_data:
        overlay_spec = overlay_data["experiment"]
    else:
        # Bare overlay — treat the whole doc (minus apiVersion/kind/metadata) as spec
        skip = {"apiVersion", "kind", "metadata"}
        overlay_spec = {k: v for k, v in overlay_data.items() if k not in skip}

    if not overlay_spec:
        logger.debug("Experiment overlay %s: empty spec, nothing to merge", overlay_path)
        return experiment_data

    merged_exp = apply_merge_patch(deepcopy(exp_block), overlay_spec)

    if "experiment" in experiment_data:
        return {**experiment_data, "experiment": merged_exp}
    return merged_exp


def apply_experiment_overlays(
    experiment_data: Dict[str, Any],
    overlay_paths: List[Path],
    *,
    base_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Apply a sequence of experiment overlays to *experiment_data*.

    Overlays are applied in order (first overlay applied first).  Each overlay
    is a deep-merge patch using RFC\u00a07386 JSON Merge Patch semantics:
    dict\u00a0+\u00a0dict \u2192 recursive merge, everything else (scalars, lists) \u2192 replace.
    To extend a pipeline list, provide the full desired list in the overlay.

    Parameters
    ----------
    experiment_data:
        Raw parsed experiment YAML dict (``{experiment: {...}}`` or bare).
    overlay_paths:
        Paths to overlay YAML files.  Relative paths are resolved from
        *base_dir* when provided, otherwise from ``cwd``.
    base_dir:
        Base directory for relative overlay paths (typically
        ``experiment_yaml.parent``).
    """
    if not overlay_paths:
        return experiment_data

    try:
        from mas.runtime.spec.source import load_yaml_file
    except ImportError:
        import yaml

        def load_yaml_file(p: Path) -> Dict[str, Any]:  # type: ignore[misc]
            return yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    result = experiment_data
    for raw_path in overlay_paths:
        p = Path(raw_path)
        if not p.is_absolute() and base_dir is not None:
            p = (base_dir / p).resolve()
        if not p.exists():
            raise FileNotFoundError(
                f"Experiment overlay not found: {p}"
            )
        try:
            overlay_data = load_yaml_file(p)
        except Exception as exc:
            raise ValueError(f"Cannot load experiment overlay {p}: {exc}") from exc

        kind = (overlay_data.get("kind") or "").strip()
        if kind and kind not in ("ExperimentOverlay", "Experiment"):
            logger.warning(
                "Experiment overlay %s has unexpected kind %r (expected ExperimentOverlay)",
                p, kind,
            )

        logger.info("Applying experiment overlay: %s", p)
        result = _apply_single_overlay(result, overlay_data, p)

    return result
