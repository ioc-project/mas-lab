#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Infer mas-lab runner id from experiment manifest and composed agent config."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from mas.lab.runners.constants import DEFAULT_LAB_RUNNER_ID, normalize_runner_id
from mas.runtime.spec.source import load_yaml_file

logger = logging.getLogger(__name__)

# ctl framework_adapter ids → mas.lab.runners entry point id.
FRAMEWORK_ADAPTER_TO_RUNNER: dict[str, str] = {
    "native": DEFAULT_LAB_RUNNER_ID,
    "mas": DEFAULT_LAB_RUNNER_ID,
    "langgraph": DEFAULT_LAB_RUNNER_ID,
    "autogen": DEFAULT_LAB_RUNNER_ID,
    "crewai": DEFAULT_LAB_RUNNER_ID,
}


def framework_adapter_from_dict(data: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract framework adapter id from a manifest or flavour dict."""
    if not isinstance(data, dict):
        return None
    spec = data.get("spec") or {}
    if not isinstance(spec, dict):
        return None

    adapter = spec.get("framework_adapter")
    if adapter:
        return str(adapter).strip().lower()

    framework = spec.get("framework")
    if isinstance(framework, dict):
        default = framework.get("default_adapter")
        if default:
            return str(default).strip().lower()

    deployment = spec.get("deployment")
    if isinstance(deployment, dict):
        dep_spec = deployment.get("spec") or deployment
        if isinstance(dep_spec, dict):
            fw = dep_spec.get("framework") or {}
            if isinstance(fw, dict) and fw.get("default_adapter"):
                return str(fw["default_adapter"]).strip().lower()

    return None


def framework_adapter_from_path(manifest_path: Path) -> Optional[str]:
    """Read framework adapter from a YAML manifest on disk."""
    if not manifest_path.is_file():
        return None
    try:
        data = load_yaml_file(manifest_path)
    except OSError:
        return None
    if not isinstance(data, dict):
        return None
    return framework_adapter_from_dict(data)


def runner_id_for_framework_adapter(adapter: Optional[str]) -> str:
    if not adapter:
        return DEFAULT_LAB_RUNNER_ID
    return FRAMEWORK_ADAPTER_TO_RUNNER.get(adapter.strip().lower(), DEFAULT_LAB_RUNNER_ID)


def infer_runner_id(
    *,
    execution_runner: Optional[str] = None,
    mas_manifest: Optional[Path] = None,
    agent_config: Optional[Dict[str, Any]] = None,
    flavour: Optional[Any] = None,
) -> str:
    """Resolve lab runner id for one execution.

    Priority:
    1. ``experiment.executor.runner`` (explicit override; leftover ``schedule.runner`` / ``execution.runner``)
    2. Composed agent manifest ``spec.framework_adapter``
    3. MAS ``mas.yaml`` (from ``applications[].app`` or ``mas.manifest``)
    4. Flavour / inline deployment ``framework.default_adapter``
    5. Default ``native`` (bench plugin ``mas.lab.benchmark.plugins.mas``)
    """
    if execution_runner:
        return normalize_runner_id(execution_runner)

    adapter = framework_adapter_from_dict(agent_config)
    if not adapter and mas_manifest:
        adapter = framework_adapter_from_path(mas_manifest)
    if not adapter and isinstance(flavour, dict):
        adapter = framework_adapter_from_dict(flavour)

    runner_id = runner_id_for_framework_adapter(adapter)
    if adapter and runner_id != DEFAULT_LAB_RUNNER_ID:
        logger.debug(
            "Inferred lab runner %r from framework_adapter=%r",
            runner_id,
            adapter,
        )
    return runner_id
