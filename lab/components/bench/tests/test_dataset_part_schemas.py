#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Optional ``spec.schemas`` on the free-form parts of dataset items."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from mas.lab.benchmark.dataset import Dataset
from mas.lab.manifests.validator import ManifestValidationError, validate_manifest

FARES_SCHEMA = {
    "type": "object",
    "required": ["fares"],
    "properties": {"fares": {"type": "array", "items": {"type": "number"}}},
}
DETAILS_SCHEMA = {"type": "object", "required": ["booked"], "properties": {"booked": {"type": "boolean"}}}


def _write(tmp_path: Path, items: list, schemas: dict) -> Path:
    path = tmp_path / "dataset.yaml"
    doc = {
        "apiVersion": "lab/v1",
        "kind": "Dataset",
        "metadata": {"name": "d"},
        "spec": {"schemas": schemas, "items": items},
    }
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


def _item(item_id: str, *, shared=None, details=None) -> dict:
    inputs: dict = {"user": "Q"}
    if shared is not None:
        inputs["tool_fixtures"] = {"by_tool": {"*": shared}}
    item = {"id": item_id, "inputs": inputs}
    if details is not None:
        item["expectations"] = {"details": details}
    return item


def test_valid_parts_pass_and_schemas_round_trip(tmp_path: Path) -> None:
    (tmp_path / "fares.schema.yaml").write_text(yaml.safe_dump(FARES_SCHEMA), encoding="utf-8")
    path = _write(
        tmp_path,
        [_item("a", shared={"fares": [12.5]}, details={"booked": True}), _item("b")],
        {
            "inputs.tool_fixtures.by_tool.get_fares": {"ref": "fares.schema.yaml"},
            "expectations.details": DETAILS_SCHEMA,
        },
    )
    ds = Dataset.from_yaml(path)
    assert ds.schemas["inputs.tool_fixtures.by_tool.get_fares"] == FARES_SCHEMA

    out = tmp_path / "out" / "dataset.yaml"
    out.parent.mkdir()
    ds.to_yaml(out)
    assert Dataset.from_yaml(out).schemas == ds.schemas


def test_tool_key_checks_the_shared_payload_the_tool_receives(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [_item("a", shared={"fares": ["cheap"]})],
        {"inputs.tool_fixtures.by_tool.get_fares": FARES_SCHEMA},
    )
    with pytest.raises(ValueError, match=r"#a: inputs\.tool_fixtures\.by_tool\.get_fares\.fares\.0"):
        Dataset.from_yaml(path)


def test_details_violation_is_reported(tmp_path: Path) -> None:
    path = _write(tmp_path, [_item("a", details={"booked": "yes"})], {"expectations.details": DETAILS_SCHEMA})
    with pytest.raises(ValueError, match="expectations.details.booked"):
        Dataset.from_yaml(path)


def test_generic_paths_cannot_carry_a_part_schema(tmp_path: Path) -> None:
    path = _write(tmp_path, [_item("a")], {"expectations.ground_truth": {"type": "string"}})
    with pytest.raises(ValueError, match="not a free-form part"):
        Dataset.from_yaml(path)


def test_missing_schema_file_fails(tmp_path: Path) -> None:
    path = _write(tmp_path, [_item("a")], {"expectations.details": "missing.schema.yaml"})
    with pytest.raises(FileNotFoundError):
        Dataset.from_yaml(path)


def test_mas_lab_validate_reports_part_violations(tmp_path: Path) -> None:
    path = _write(tmp_path, [_item("a", details={"booked": 1})], {"expectations.details": DETAILS_SCHEMA})
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    with pytest.raises(ManifestValidationError, match="expectations.details.booked"):
        validate_manifest(data, source=str(path), kind="dataset", resolve_refs=True)


def test_mas_lab_validate_rejects_bad_schema_key(tmp_path: Path) -> None:
    path = _write(tmp_path, [_item("a")], {"inputs.user": {"type": "string"}})
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    with pytest.raises(ManifestValidationError):
        validate_manifest(data, source=str(path), kind="dataset", resolve_refs=True)
