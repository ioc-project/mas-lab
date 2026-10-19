#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shipped experiments and datasets must not use deprecated envelope shapes."""
from __future__ import annotations

import warnings
from pathlib import Path

import pytest
import yaml

from mas.lab.benchmark.dataset import Dataset
from mas.lab.deprecations import clear_deprecation_warnings

REPO = Path(__file__).resolve().parents[4]

_LEGACY_EXPERIMENT_KEYS = ("execution", "mas", "applications", "test")

_EXPERIMENT_GLOBS = (
    "labs/**/experiment*.yaml",
    "library-samples/experiments/**/*.yaml",
    "docs/tutorials/**/experiment*.yaml",
    "tests/fixtures/**/*.yaml",
    "lab/components/controller/tests/fixtures/**/*.yaml",
    "docs/schemas/examples/experiments/**/*.yaml",
)

_DATASET_GLOBS = (
    "library-samples/datasets/**/*.yaml",
    "labs/**/datasets/**/*.yaml",
    "docs/tutorials/**/dataset*.yaml",
    "tests/fixtures/**/dataset.yaml",
    "lab/components/controller/tests/fixtures/**/datasets/**/*.yaml",
    "docs/schemas/examples/datasets/**/*.yaml",
)


def _load_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _experiment_paths() -> list[Path]:
    paths: list[Path] = []
    for pattern in _EXPERIMENT_GLOBS:
        paths.extend(REPO.glob(pattern))
    out: list[Path] = []
    for path in sorted(set(paths)):
        if path.name.endswith(".schema.yaml"):
            continue
        data = _load_yaml(path)
        if isinstance(data, dict) and (
            "experiment" in data or data.get("kind") == "Experiment"
        ):
            out.append(path)
    return out


def _dataset_paths() -> list[Path]:
    paths: list[Path] = []
    for pattern in _DATASET_GLOBS:
        paths.extend(REPO.glob(pattern))
    out: list[Path] = []
    for path in sorted(set(paths)):
        data = _load_yaml(path)
        if not isinstance(data, dict) or data.get("kind") != "Dataset":
            continue
        spec = data.get("spec") or {}
        if spec.get("source") and not spec.get("items"):
            continue
        out.append(path)
    return out


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO))


@pytest.mark.parametrize("path", _experiment_paths(), ids=_rel)
def test_shipped_experiments_have_no_legacy_keys(path: Path) -> None:
    data = _load_yaml(path)
    assert isinstance(data, dict)
    exp = data["experiment"] if "experiment" in data else data
    leftover = [key for key in _LEGACY_EXPERIMENT_KEYS if key in exp]
    assert leftover == [], f"{_rel(path)} still has deprecated keys {leftover}"


@pytest.mark.parametrize("path", _dataset_paths(), ids=_rel)
def test_shipped_datasets_load_without_deprecation(path: Path) -> None:
    clear_deprecation_warnings()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        Dataset.from_yaml(path)
    leftover = [
        str(w.message)
        for w in caught
        if issubclass(w.category, DeprecationWarning)
        and str(w.message).startswith("Deprecated:")
    ]
    assert leftover == [], f"{_rel(path)} still uses a deprecated dataset shape: {leftover}"
