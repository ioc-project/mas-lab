#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Strict validation gate for paper labs — experiments live under labs/ only."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from mas.lab.manifests.validator import validate_manifest

_ROOT = Path(__file__).resolve().parents[1]


def _experiment_paths() -> list[Path]:
    return sorted(_ROOT.glob("labs/**/experiment*.yaml"))


def test_no_experiments_in_library_samples() -> None:
    """Experiments are lab-scoped; library-samples holds reusable artefacts only."""
    stray = sorted(_ROOT.glob("library-samples/**/experiment*.yaml"))
    assert not stray, "experiment manifests must live under labs/, not library-samples: " + ", ".join(
        str(p.relative_to(_ROOT)) for p in stray
    )


@pytest.mark.parametrize(
    "path",
    _experiment_paths(),
    ids=lambda p: str(p.relative_to(_ROOT)),
)
def test_lab_experiment_strict_schema(path: Path) -> None:
    pytest.importorskip("jsonschema")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    validate_manifest(
        data,
        source=str(path.relative_to(_ROOT)),
        kind="experiment",
        strict=True,
        resolve_refs=True,
        base_dir=path.parent,
    )


def _dataset_paths() -> list[Path]:
    paths = sorted(_ROOT.glob("docs/tutorials/**/dataset*.yaml"))
    paths += sorted(_ROOT.glob("library-samples/**/dataset.yaml"))
    return paths


@pytest.mark.parametrize(
    "path",
    _dataset_paths(),
    ids=lambda p: str(p.relative_to(_ROOT)),
)
def test_lab_dataset_strict_schema(path: Path) -> None:
    pytest.importorskip("jsonschema")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    validate_manifest(
        data,
        source=str(path.relative_to(_ROOT)),
        kind="dataset",
        strict=True,
        resolve_refs=False,
        base_dir=path.parent,
    )


def test_lab_config_legacy_fields_strict_schema() -> None:
    pytest.importorskip("jsonschema")
    validate_manifest(
        {
            "lab": {
                "name": "legacy-lab",
                "default_flavour": "local",
                "output_dir": "./legacy-runs",
                "scenarios": [{"id": "baseline", "user_prompt": "Start with the legacy prompt"}],
            }
        },
        source="lab-config.yaml",
        kind="lab-config",
        strict=True,
        resolve_refs=False,
    )
