#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for ExperimentConfig — specifically name-based dataset resolution."""

import sys
import tempfile
from pathlib import Path

import pytest
import yaml
from mas.lab.benchmark.experiment import _resolve_dataset_by_name, _resolve_dataset_from_package


def _make_dataset_manifest(tmpdir: Path, filename: str, name: str, items: list) -> Path:
    """Write a minimal Dataset manifest to *tmpdir/datasets/<filename>*."""
    datasets_dir = tmpdir / "datasets"
    datasets_dir.mkdir(parents=True, exist_ok=True)
    path = datasets_dir / filename
    manifest = {
        "apiVersion": "lab/v1",
        "kind": "Dataset",
        "metadata": {"name": name, "version": "1.0"},
        "spec": {"items": items},
    }
    path.write_text(yaml.dump(manifest, allow_unicode=True, sort_keys=False))
    return path


# ── Local resolution (locator absent / "local") ──────────────────────────────


def test_resolve_dataset_by_name_in_datasets_folder():
    """A manifest in datasets/ is resolved by metadata.name."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        _make_dataset_manifest(
            base,
            "trip-queries.yaml",
            "trip-planner-queries",
            [{"id": "q1", "prompt": "Plan a trip"}],
        )
        resolved = _resolve_dataset_by_name(base, "trip-planner-queries")
        assert resolved == base / "datasets" / "trip-queries.yaml"


def test_resolve_dataset_by_name_walks_up_past_nested_lab_config():
    """Nested experiment.yaml still finds datasets/ at the lab root.

    Sub-experiments may have their own lab-config.yaml; keep walking.
    """
    with tempfile.TemporaryDirectory() as tmp:
        lab = Path(tmp)
        (lab / "lab-config.yaml").write_text("lab:\n  name: example\n")
        expected = _make_dataset_manifest(
            lab,
            "queries.yaml",
            "trip-planner-queries",
            [{"id": "q1", "inputs": {"user": "Plan a trip"}}],
        )
        nested = lab / "02-bit-exactness"
        nested.mkdir()
        (nested / "lab-config.yaml").write_text("lab:\n  name: nested\n")
        resolved = _resolve_dataset_by_name(nested, "trip-planner-queries")
        assert resolved == expected


def test_resolve_dataset_by_name_root_fallback():
    """A manifest at base_dir root (no datasets/ subfolder) is found as fallback."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        path = base / "dataset.yaml"
        manifest = {
            "apiVersion": "lab/v1",
            "kind": "Dataset",
            "metadata": {"name": "my-eval"},
            "spec": {"items": [{"id": "1", "prompt": "Q"}]},
        }
        path.write_text(yaml.dump(manifest))
        resolved = _resolve_dataset_by_name(base, "my-eval")
        assert resolved == path


def test_resolve_dataset_by_name_stem_fallback():
    """An unlabelled file (no metadata.name) is matched by stem == name."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        datasets_dir = base / "datasets"
        datasets_dir.mkdir()
        path = datasets_dir / "my-eval.yaml"
        path.write_text(yaml.dump({"items": [{"id": "1", "prompt": "Q"}]}))
        resolved = _resolve_dataset_by_name(base, "my-eval")
        assert resolved == path


def test_resolve_dataset_not_found_raises():
    """FileNotFoundError is raised when no match is found."""
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(FileNotFoundError, match="no-such-dataset"):
            _resolve_dataset_by_name(Path(tmp), "no-such-dataset")


def test_resolve_dataset_multiple_manifests_correct_one():
    """The correct manifest is found when multiple manifests exist in datasets/."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        _make_dataset_manifest(base, "a.yaml", "dataset-alpha", [{"id": "a1", "prompt": "PA"}])
        _make_dataset_manifest(base, "b.yaml", "dataset-beta", [{"id": "b1", "prompt": "PB"}])
        _make_dataset_manifest(base, "c.yaml", "dataset-gamma", [{"id": "c1", "prompt": "PC"}])

        assert _resolve_dataset_by_name(base, "dataset-alpha") == base / "datasets" / "a.yaml"
        assert _resolve_dataset_by_name(base, "dataset-beta") == base / "datasets" / "b.yaml"
        assert _resolve_dataset_by_name(base, "dataset-gamma") == base / "datasets" / "c.yaml"


def test_resolve_dataset_explicit_local_locator():
    """locator='local' behaves identically to the default (no locator)."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        _make_dataset_manifest(base, "q.yaml", "my-queries", [{"id": "1", "prompt": "Q"}])
        assert _resolve_dataset_by_name(base, "my-queries", locator="local") == _resolve_dataset_by_name(
            base, "my-queries"
        )


# ── Package / library locator ─────────────────────────────────────────────────


def test_resolve_dataset_from_package_via_syspath(tmp_path):
    """A library path added to sys.path makes its datasets/ findable by locator."""
    # Simulate a library package installed at tmp_path/my_lib/
    lib_root = tmp_path / "my_lib"
    _make_dataset_manifest(lib_root, "shared.yaml", "shared-queries", [{"id": "s1", "prompt": "Shared Q"}])

    # Inject the library root into sys.path (as mas-lab would via workspace libraries:)
    sys.path.insert(0, str(tmp_path))
    try:
        # With locator="my_lib", the resolver finds the dataset in my_lib/datasets/
        resolved = _resolve_dataset_by_name(Path("/nonexistent"), "shared-queries", locator="my_lib")
        assert resolved == lib_root / "datasets" / "shared.yaml"
    finally:
        sys.path.remove(str(tmp_path))


def test_resolve_dataset_from_package_not_found_raises(tmp_path):
    """FileNotFoundError with package name when locator package has no match."""
    with pytest.raises(FileNotFoundError, match="nonexistent-pkg"):
        _resolve_dataset_from_package("ghost-dataset", "nonexistent-pkg")


def test_resolve_dataset_package_locator_preferred_over_local(tmp_path):
    """When locator is given, only that package's datasets/ is scanned (not lab local)."""
    # Lab has a dataset with the same name
    lab_base = tmp_path / "lab"
    _make_dataset_manifest(lab_base, "local.yaml", "common-queries", [{"id": "local", "prompt": "Local"}])

    # Library package has a different file for the same name
    lib_root = tmp_path / "my_lib2"
    _make_dataset_manifest(lib_root, "shared.yaml", "common-queries", [{"id": "shared", "prompt": "Shared"}])

    sys.path.insert(0, str(tmp_path))
    try:
        resolved = _resolve_dataset_by_name(lab_base, "common-queries", locator="my_lib2")
        # Should find the library version, not the local one
        assert resolved == lib_root / "datasets" / "shared.yaml"
    finally:
        sys.path.remove(str(tmp_path))
