#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Versioned manifest loader.

Loads experiment and pipeline YAML, validates the declared format version, and
returns the parsed document. Only the current format version is supported in this
release.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple

from mas.lab.manifests.versions import assert_supported, detect_version
from mas.runtime.secrets import load_checked_document


def load_experiment_data(path: Path) -> Tuple[Dict[str, Any], str]:
    """Parse an experiment YAML file. A literal secret or a credential in a URL is refused."""
    raw = _read_yaml(path)
    version = detect_version(raw)
    assert_supported(version, path)
    return raw, version


def load_pipeline_data(path: Path) -> Tuple[Dict[str, Any], str]:
    """Parse a pipeline YAML file."""
    raw = _read_yaml(path)
    version = detect_version(raw)
    assert_supported(version, path)
    return raw, version


def normalize_manifest_version(
    raw: Dict[str, Any],
    manifest_type: str,
    path: Path,
) -> Tuple[Dict[str, Any], str]:
    """Detect manifest version for pre-loaded YAML data."""
    version = detect_version(raw)
    assert_supported(version, path)
    return raw, version


def _read_yaml(path: Path) -> Dict[str, Any]:
    return load_checked_document(path)
