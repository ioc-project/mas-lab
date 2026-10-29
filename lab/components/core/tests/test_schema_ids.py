#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Schema registry indexes vocabulary ``$id`` values."""
from __future__ import annotations

import os
from pathlib import Path

from mas.lab.schemas.validate import (
    _REGISTRY_CACHE,
    clear_schema_registry_cache,
    schema_registry_from_dir,
)

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


def test_schema_registry_keeps_the_cache_when_only_mtime_changes(tmp_path: Path) -> None:
    path = tmp_path / "sample.schema.yaml"
    path.write_text("$id: mas-test/mtime\ntype: object\n", encoding="utf-8")
    first = schema_registry_from_dir(tmp_path)
    os.utime(path, (path.stat().st_atime + 5, path.stat().st_mtime + 5))
    second = schema_registry_from_dir(tmp_path)
    assert first is second


def test_schema_registry_rebuilds_when_only_size_changes(tmp_path: Path) -> None:
    path = tmp_path / "sample.schema.yaml"
    path.write_text("$id: mas-test/size\ntype: object\n", encoding="utf-8")
    first = schema_registry_from_dir(tmp_path)
    mtime_ns = path.stat().st_mtime_ns
    path.write_text("$id: mas-test/size\ntype: object\ntitle: grown\n", encoding="utf-8")
    os.utime(path, ns=(path.stat().st_atime_ns, mtime_ns))
    assert path.stat().st_mtime_ns == mtime_ns
    second = schema_registry_from_dir(tmp_path)
    assert first is not second
    assert second.contents("mas-test/size")["title"] == "grown"


def test_schema_registry_rebuilds_on_same_size_rewrite_with_mtime_held(tmp_path: Path) -> None:
    path = tmp_path / "sample.schema.yaml"
    path.write_text("$id: mas-test/same\ntype: object\n", encoding="utf-8")
    first = schema_registry_from_dir(tmp_path)
    assert first.contents("mas-test/same")["type"] == "object"
    mtime_ns = path.stat().st_mtime_ns
    size = path.stat().st_size
    path.write_text("$id: mas-test/same\ntype: string\n", encoding="utf-8")
    os.utime(path, ns=(path.stat().st_atime_ns, mtime_ns))
    assert path.stat().st_mtime_ns == mtime_ns
    assert path.stat().st_size == size
    second = schema_registry_from_dir(tmp_path)
    assert first is not second
    assert second.contents("mas-test/same")["type"] == "string"


def test_clear_schema_registry_cache_empties_the_cache(tmp_path: Path) -> None:
    path = tmp_path / "sample.schema.yaml"
    path.write_text("$id: mas-test/clear\ntype: object\n", encoding="utf-8")
    schema_registry_from_dir(tmp_path)
    assert _REGISTRY_CACHE
    clear_schema_registry_cache()
    assert not _REGISTRY_CACHE


def test_registry_indexes_canonical_ids() -> None:
    registry = schema_registry_from_dir(REPO_ROOT / "docs" / "schemas")
    assert registry.contents("mas/v1#llm-model-catalog")["title"] == "LlmModelCatalog"
    assert registry.contents("mas-lab/pipeline-manifest/v1")["title"] == "Pipeline Manifest"
    assert registry.contents("mas-lab/evaluation-envelope/v0.1")["title"] == "EvaluationEnvelope"
