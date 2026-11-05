#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import tomllib
from pathlib import Path

import yaml
from mas.lab.runners.constants import (
    DEFAULT_LAB_RUNNER_ID,
    normalize_runner_id,
)

REPO_ROOT = Path(__file__).resolve().parents[4]


def test_default_lab_runner_id() -> None:
    assert DEFAULT_LAB_RUNNER_ID == "native"


def test_normalize_runner_id_strips_whitespace() -> None:
    assert normalize_runner_id(" native ") == "native"
    assert normalize_runner_id("native") == "native"


def test_runner_id_entry_point_and_schema_alignment() -> None:
    """Runner id stays aligned across constants, entry points, schema, and classes."""
    from mas.ctl.benchmark.runner import MasBenchRunner
    from mas.lab.benchmark.plugins.mas import MasRuntimeRunner

    bench_toml = REPO_ROOT / "lab" / "components" / "bench" / "pyproject.toml"
    data = tomllib.loads(bench_toml.read_text(encoding="utf-8"))
    runners = data.get("project", {}).get("entry-points", {}).get("mas.lab.runners", {})
    assert DEFAULT_LAB_RUNNER_ID in runners

    schema_path = REPO_ROOT / "docs" / "schemas" / "lab" / "experiment.schema.yaml"
    schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
    runner_schema = schema["properties"]["experiment"]["properties"]["execution"]["properties"]["runner"]
    assert runner_schema["default"] == DEFAULT_LAB_RUNNER_ID

    assert MasRuntimeRunner.runner_id == DEFAULT_LAB_RUNNER_ID
    assert MasBenchRunner.runner_id == DEFAULT_LAB_RUNNER_ID
