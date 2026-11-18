#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from mas.runtime.package_refs import path_ref_for_anchor
from mas.runtime.spec.infra_paths import experiment_infra_bundle_path, resolve_infra_bundle


def test_path_ref_for_anchor_relative(tmp_path: Path):
    anchor = tmp_path / "app"
    anchor.mkdir()
    bundle = anchor / "infra" / "gls.yaml"
    bundle.parent.mkdir()
    bundle.write_text("{}", encoding="utf-8")
    assert path_ref_for_anchor(bundle, anchor) == "infra/gls.yaml"


def test_experiment_infra_bundle_path(tmp_path: Path):
    exp = tmp_path / "labs" / "sample"
    bundle = exp / "infra" / "gls-vllm.yaml"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("{}", encoding="utf-8")
    assert experiment_infra_bundle_path(exp, "gls-vllm") == bundle
    assert experiment_infra_bundle_path(exp, "missing") is None


def test_resolve_infra_bundle_workspace_search(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "pyproject.toml").write_text("", encoding="utf-8")
    shared = ws / "infra" / "services.yaml"
    shared.parent.mkdir()
    shared.write_text("{}", encoding="utf-8")
    exp = ws / "labs" / "x"
    exp.mkdir(parents=True)
    assert resolve_infra_bundle(exp, "services", search_workspace=True) == shared
