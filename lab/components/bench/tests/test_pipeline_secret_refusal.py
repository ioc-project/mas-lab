#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Pipeline files (with their ``!include`` files) and infra stores get the manifest secret checks when they load."""

from __future__ import annotations

import traceback
from pathlib import Path

import pytest
from mas.lab.benchmark.pipeline.core import MAX_INCLUDE_DEPTH, Pipeline, PipelineLoadError
from mas.lab.benchmark.schedule.pipeline_resolve import resolve_pipeline_specs_from_yaml
from mas.runtime.secrets import SecretRefError

CANARY = "sk-S3CR3T-pipeline-canary"
_LINES = {"literal": f"api_key: {CANARY}", "url": f"endpoint: https://u:{CANARY}@otel.example:4318"}


def _pipeline(tmp_path: Path, config: str) -> Path:
    path = tmp_path / "lab" / "pipeline.yaml"
    path.parent.mkdir(exist_ok=True)
    step = f"  - name: export\n    type: export_otel\n    config: {config}\n"
    path.write_text(f"pipeline:\n  name: p\n  steps:\n{step}", encoding="utf-8")
    return path


def _refused(path: Path, error: type[Exception]) -> str:
    texts = []
    for call in (Pipeline.step_dicts_from_yaml, Pipeline.from_yaml, resolve_pipeline_specs_from_yaml):
        with pytest.raises(error) as caught:
            call(path)
        texts.append("".join(traceback.format_exception(caught.value)))
    assert CANARY not in "".join(texts)
    return "".join(texts)


@pytest.mark.parametrize("case", sorted(_LINES))
def test_a_secret_in_an_include_does_not_load(tmp_path: Path, case: str) -> None:
    path = _pipeline(tmp_path, "!include steps.yaml")
    (path.parent / "steps.yaml").write_text(_LINES[case] + "\n", encoding="utf-8")
    assert str(path) in _refused(path, SecretRefError)


def test_nested_includes_and_a_secret_ref_load(tmp_path: Path) -> None:
    path = _pipeline(tmp_path, "!include parts/config.yaml")
    (path.parent / "parts").mkdir()
    (path.parent / "parts" / "config.yaml").write_text("inner: !include inner.yaml\n", encoding="utf-8")
    (path.parent / "parts" / "inner.yaml").write_text("api_key: {secret: otel}\n", encoding="utf-8")
    [step] = Pipeline.step_dicts_from_yaml(path)
    assert step["config"] == {"inner": {"api_key": {"secret": "otel"}}}


@pytest.mark.parametrize(
    "target", ["../outside.yaml", "/abs/outside.yaml", "escape.yaml", "notes.txt", "missing.yaml", f"*{CANARY}"]
)
def test_an_include_outside_the_pipeline_directory_or_a_broken_one_is_refused(tmp_path: Path, target: str) -> None:
    (tmp_path / "outside.yaml").write_text(f"note: {CANARY}\n", encoding="utf-8")
    path = _pipeline(tmp_path, f"!include {target}")
    (path.parent / "escape.yaml").symlink_to(tmp_path / "outside.yaml")
    (path.parent / "notes.txt").write_text(f"note: {CANARY}\n", encoding="utf-8")
    assert "(line " in _refused(path, PipelineLoadError)


def test_include_depth_is_capped(tmp_path: Path) -> None:
    path = _pipeline(tmp_path, "!include d0.yaml")
    for level in range(MAX_INCLUDE_DEPTH + 1):
        (path.parent / f"d{level}.yaml").write_text(f"next: !include d{level + 1}.yaml\n", encoding="utf-8")
    assert f"nested more than {MAX_INCLUDE_DEPTH} deep" in _refused(path, PipelineLoadError)


@pytest.mark.parametrize("case", sorted(_LINES))
def test_an_infra_store_file_with_a_secret_does_not_load(tmp_path: Path, case: str) -> None:
    from mas.lab.infra.stores import resolve_datastore

    (tmp_path / ".git").mkdir()
    (tmp_path / "config.yaml").write_text("mas_lab:\n  infra: stores.yaml\n", encoding="utf-8")
    line = _LINES[case].replace("api_key", "password")
    stores = f"kind: Datastore\nspec:\n  stores:\n    kg:\n      kind: neo4j\n      {line}\n"
    (tmp_path / "stores.yaml").write_text(stores, encoding="utf-8")
    with pytest.raises(SecretRefError) as caught:
        resolve_datastore("kg", anchor=tmp_path)
    assert "stores.yaml" in str(caught.value) and CANARY not in str(caught.value)
