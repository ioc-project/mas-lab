#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from pathlib import Path

import pytest
from fastapi import HTTPException
from mas.lab.controller.routes.experiments import resolve_experiment_path


def test_resolve_experiment_by_yaml_name(tmp_path: Path) -> None:
    (tmp_path / "experiments").mkdir()
    dest = tmp_path / "experiments" / "demo-exp.yaml"
    dest.write_text("experiment:\n  name: demo-exp\n", encoding="utf-8")
    assert resolve_experiment_path(tmp_path, "demo-exp") == dest


def test_resolve_experiment_root_file_by_inner_name(tmp_path: Path) -> None:
    dest = tmp_path / "experiment.yaml"
    dest.write_text("experiment:\n  name: demo-exp\n", encoding="utf-8")
    assert resolve_experiment_path(tmp_path, "demo-exp") == dest


def test_resolve_experiment_missing(tmp_path: Path) -> None:
    with pytest.raises(HTTPException) as exc:
        resolve_experiment_path(tmp_path, "missing")
    assert exc.value.status_code == 404
