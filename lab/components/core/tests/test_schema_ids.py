#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Schema registry indexes vocabulary ``$id`` values."""
from __future__ import annotations

from pathlib import Path

from mas.lab.schemas.validate import schema_registry_from_dir

REPO_ROOT = Path(__file__).resolve().parents[4]


def test_registry_indexes_canonical_ids() -> None:
    registry = schema_registry_from_dir(REPO_ROOT / "docs" / "schemas")
    assert registry.contents("mas/v1#llm-model-catalog")["title"] == "LlmModelCatalog"
    assert registry.contents("mas-lab/pipeline-manifest/v1")["title"] == "Pipeline Manifest"
    assert registry.contents("mas-lab/evaluation-envelope/v0.1")["title"] == "EvaluationEnvelope"
