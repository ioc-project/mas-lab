#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Unit tests for golden lab target resolution."""

from __future__ import annotations

from pathlib import Path

from mas.lab.benchmark.golden.labs import (
    default_label_for,
    find_experiment_yaml,
    load_labs_manifest,
    resolve_lab_spec,
    resolve_lab_targets,
)

ROOT = Path(__file__).resolve().parents[4]


def test_find_experiment_yaml_nested_lab():
    exp = find_experiment_yaml(ROOT / "labs/design-space.lab")
    assert exp is not None
    assert exp.name == "experiment.yaml"


def test_resolve_lab_targets_all_from_manifest():
    manifest = ROOT / "tests/fixtures/golden-runs/labs.yaml"
    targets = resolve_lab_targets(["all"], root=ROOT, manifest_path=manifest)
    labels = {t[0] for t in targets}
    assert "extensions" in labels
    assert "design-space" in labels


def test_default_label_for_experiment_path():
    exp = ROOT / "labs/extensions.lab/experiment.yaml"
    assert default_label_for(exp, root=ROOT) == "extensions"


def test_resolve_lab_spec_by_manifest_label():
    manifest = load_labs_manifest(ROOT / "tests/fixtures/golden-runs/labs.yaml", root=ROOT)
    label, path = resolve_lab_spec("lifecycle-control", root=ROOT, manifest=manifest)
    assert label == "lifecycle-control"
    assert path.is_file()
