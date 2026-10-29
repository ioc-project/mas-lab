#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Schema registry indexes vocabulary ``$id`` values."""
from __future__ import annotations

from pathlib import Path

from mas.lab.schemas.validate import schema_registry_from_dir

REPO_ROOT = Path(__file__).resolve().parents[4]


def test_schema_registry_from_dir_is_reused() -> None:
    root = REPO_ROOT / "docs" / "schemas"
    first = schema_registry_from_dir(root)
    second = schema_registry_from_dir(root)
    assert first is second


def test_schema_registry_from_dir_rebuilds_when_a_file_changes(tmp_path: Path) -> None:
    path = tmp_path / "sample.schema.yaml"
    path.write_text("$id: mas-test/tmp\ntype: object\n", encoding="utf-8")
    first = schema_registry_from_dir(tmp_path)
    assert first.contents("mas-test/tmp")["type"] == "object"
    path.write_text("$id: mas-test/tmp\ntype: object\ntitle: changed\n", encoding="utf-8")
    second = schema_registry_from_dir(tmp_path)
    assert first is not second
    assert second.contents("mas-test/tmp")["title"] == "changed"


def test_registry_indexes_canonical_ids() -> None:
    registry = schema_registry_from_dir(REPO_ROOT / "docs" / "schemas")
    assert registry.contents("mas/v1#llm-model-catalog")["title"] == "LlmModelCatalog"
    assert registry.contents("mas-lab/pipeline-manifest/v1")["title"] == "Pipeline Manifest"
    assert registry.contents("mas-lab/evaluation-envelope/v0.1")["title"] == "EvaluationEnvelope"
