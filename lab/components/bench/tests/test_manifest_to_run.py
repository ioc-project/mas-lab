#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The application manifest an experiment names is the manifest that runs."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.lab.manifests.ref_checks import check_composed_mas, manifest_to_run


def _app_dir(tmp_path: Path, payer_kind: str | None) -> Path:
    app = tmp_path / "app"
    app.mkdir()
    (app / "mas.yaml").write_text("kind: mas\nmetadata:\n  name: other\n", encoding="utf-8")
    body = f"kind: {payer_kind}\n" if payer_kind else "name: payer\n"
    (app / "payer.yaml").write_text(body, encoding="utf-8")
    return app / "payer.yaml"


def test_named_manifest_wins_over_sibling(tmp_path: Path):
    payer = _app_dir(tmp_path, "app")
    assert manifest_to_run(payer) == payer


def test_unrecognised_kind_refused_naming_file_and_sibling(tmp_path: Path):
    payer = _app_dir(tmp_path, None)
    with pytest.raises(ValueError) as err:
        manifest_to_run(payer)
    assert "payer.yaml" in str(err.value) and "mas.yaml is NOT used" in str(err.value)


def test_validation_reports_the_refusal(tmp_path: Path):
    payer = _app_dir(tmp_path, None)
    violations = check_composed_mas({"mas": {"manifest": str(payer)}}, tmp_path, source="exp")
    assert any("payer.yaml" in v and "NOT used" in v for v in violations)


def test_unrecognised_kind_refused_when_the_sibling_is_mas_yml(tmp_path: Path):
    app = tmp_path / "app"
    app.mkdir()
    (app / "mas.yml").write_text("kind: mas\nmetadata:\n  name: other\n", encoding="utf-8")
    payer = app / "payer.yaml"
    payer.write_text("name: payer\n", encoding="utf-8")
    with pytest.raises(ValueError) as err:
        manifest_to_run(payer)
    assert "mas.yml is NOT used" in str(err.value)
