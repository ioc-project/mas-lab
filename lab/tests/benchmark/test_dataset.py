#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for benchmark dataset module."""

import json
import tempfile
from pathlib import Path

import pytest
import yaml
from mas.lab.benchmark import Dataset, DatasetItem
from mas.lab.inputs import RunInput


def test_dataset_item_from_dict():
    """Test DatasetItem creation from envelope dictionary."""
    data = {
        "id": "001",
        "inputs": {
            "user": "What is 2+2?",
        },
        "expectations": {"ground_truth": "4"},
        "category": "math",
        "difficulty": "easy",
    }

    item = DatasetItem.from_dict(data)

    assert item.id == "001"
    assert item.prompt == "What is 2+2?"
    assert item.run_input.expectations["ground_truth"] == "4"
    assert item.metadata["category"] == "math"
    assert item.metadata["difficulty"] == "easy"


def test_dataset_from_yaml():
    """Test Dataset loading from manifest format (lab/v1)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        manifest = {
            "apiVersion": "lab/v1",
            "kind": "Dataset",
            "metadata": {
                "name": "manifest-dataset",
                "version": "2.0",
                "description": "Manifest-wrapped dataset",
                "tags": ["test"],
            },
            "spec": {
                "items": [
                    {
                        "id": "m1",
                        "inputs": {"user": "MQ1"},
                        "category": "travel",
                    },
                    {
                        "id": "m2",
                        "inputs": {"user": "MQ2"},
                        "category": "travel",
                    },
                    {
                        "id": "m3",
                        "inputs": {"user": "MQ3"},
                        "category": "tech",
                    },
                ]
            },
        }
        yaml.dump(manifest, f)
        temp_path = Path(f.name)

    try:
        dataset = Dataset.from_yaml(temp_path)
        assert dataset.name == "manifest-dataset"
        assert dataset.version == "2.0"
        assert len(dataset) == 3
        assert dataset[0].id == "m1"
        assert dataset[2].metadata["category"] == "tech"
    finally:
        temp_path.unlink()


def test_dataset_filter():
    """Test dataset filtering on metadata fields."""
    items = [
        DatasetItem(
            id="001",
            run_input=RunInput(user=[{"role": "user", "content": "Q1"}]),
            metadata={"category": "math"},
        ),
        DatasetItem(
            id="002",
            run_input=RunInput(user=[{"role": "user", "content": "Q2"}]),
            metadata={"category": "logic"},
        ),
        DatasetItem(
            id="003",
            run_input=RunInput(user=[{"role": "user", "content": "Q3"}]),
            metadata={"category": "math"},
        ),
    ]

    dataset = Dataset(name="test", items=items)
    filtered = dataset.filter(category="math")

    assert len(filtered) == 2
    assert filtered[0].id == "001"
    assert filtered[1].id == "003"


def test_dataset_iteration():
    """Test dataset iteration."""
    items = [
        DatasetItem(
            id="001",
            run_input=RunInput(user=[{"role": "user", "content": "Q1"}]),
        ),
        DatasetItem(
            id="002",
            run_input=RunInput(user=[{"role": "user", "content": "Q2"}]),
        ),
    ]

    dataset = Dataset(name="test", items=items)

    item_ids = [item.id for item in dataset]
    assert item_ids == ["001", "002"]


def test_dataset_source_jsonl_is_a_meta_dataset(tmp_path: Path):
    """spec.source maps a third-party file; it does not copy the corpus into git."""
    src = tmp_path / "mmlu.jsonl"
    src.write_text(
        json.dumps(
            {
                "question_id": "q1",
                "question": "What is 2+2?",
                "options": ["3", "4", "5"],
                "answer_index": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "dataset.yaml"
    yaml.dump(
        {
            "apiVersion": "lab/v1",
            "kind": "Dataset",
            "metadata": {"name": "mmlu-pro", "version": "v1"},
            "spec": {
                "source": {
                    "kind": "jsonl",
                    "path": "mmlu.jsonl",
                    "map": {
                        "id": "question_id",
                        "inputs.user": "{question}\n{options_text}",
                        "expectations.ground_truth": "answer_index",
                    },
                }
            },
        },
        manifest.open("w"),
    )
    dataset = Dataset.from_yaml(manifest)
    assert len(dataset) == 1
    assert dataset[0].id == "q1"
    assert "What is 2+2?" in dataset[0].prompt
    assert "B. 4" in dataset[0].prompt
    assert dataset[0].run_input.expectations["ground_truth"] == 1


def test_dataset_source_without_path_errors(tmp_path: Path):
    manifest = tmp_path / "dataset.yaml"
    yaml.dump(
        {
            "apiVersion": "lab/v1",
            "kind": "Dataset",
            "metadata": {"name": "mmlu-pro", "version": "v1"},
            "spec": {"source": {"kind": "jsonl"}},
        },
        manifest.open("w"),
    )
    with pytest.raises(ValueError, match="path is required"):
        Dataset.from_yaml(manifest)


def test_spec_path_loads_yaml_sidecar(tmp_path: Path):
    (tmp_path / "rows.yaml").write_text(
        yaml.dump({"items": [{"id": "1", "inputs": {"user": "from sidecar"}, "group": "single_agent"}]}),
        encoding="utf-8",
    )
    manifest = tmp_path / "dataset.yaml"
    yaml.dump(
        {
            "apiVersion": "lab/v1",
            "kind": "Dataset",
            "metadata": {"name": "sidecar", "version": "v1"},
            "spec": {"path": "rows.yaml", "format": "yaml"},
        },
        manifest.open("w"),
    )
    dataset = Dataset.from_yaml(manifest)
    assert len(dataset) == 1
    assert dataset[0].id == "1"
    assert dataset[0].prompt == "from sidecar"


def test_spec_path_rejects_json(tmp_path: Path):
    (tmp_path / "rows.json").write_text("[]", encoding="utf-8")
    manifest = tmp_path / "dataset.yaml"
    yaml.dump(
        {
            "apiVersion": "lab/v1",
            "kind": "Dataset",
            "metadata": {"name": "json-gone"},
            "spec": {"path": "rows.json"},
        },
        manifest.open("w"),
    )
    with pytest.raises(ValueError, match="no longer accepts JSON"):
        Dataset.from_yaml(manifest)


def test_dataset_item_without_id_still_loads(tmp_path: Path):
    manifest = tmp_path / "dataset.yaml"
    yaml.dump(
        {
            "apiVersion": "lab/v1",
            "kind": "Dataset",
            "metadata": {"name": "noid"},
            "spec": {"items": [{"prompt": "hello"}]},
        },
        manifest.open("w"),
    )
    dataset = Dataset.from_yaml(manifest)
    assert len(dataset) == 1
    assert dataset[0].id == "item"
    assert dataset[0].prompt == "hello"


def test_legacy_item_list_yaml_still_loads(tmp_path: Path):
    path = tmp_path / "queries.yaml"
    yaml.dump([{"id": "q1", "prompt": "Q1"}, {"id": "q2", "query": "Q2"}], path.open("w"))
    dataset = Dataset.from_yaml(path)
    assert [item.prompt for item in dataset] == ["Q1", "Q2"]


def test_missing_spec_path_loads_empty(tmp_path: Path):
    manifest = tmp_path / "dataset.yaml"
    yaml.dump(
        {
            "apiVersion": "lab/v1",
            "kind": "Dataset",
            "metadata": {"name": "missing-sidecar"},
            "spec": {"path": "no-such-file.yaml"},
        },
        manifest.open("w"),
    )
    dataset = Dataset.from_yaml(manifest)
    assert list(dataset) == []


def test_empty_dataset_yaml_loads():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(
            {
                "apiVersion": "lab/v1",
                "kind": "Dataset",
                "metadata": {"name": "empty"},
                "spec": {"items": []},
            },
            f,
        )
        temp_path = Path(f.name)
    try:
        dataset = Dataset.from_yaml(temp_path)
        assert list(dataset) == []
    finally:
        temp_path.unlink()


def test_cataloged_trip_planner_benchmark_loads():
    path = Path(__file__).resolve().parents[3] / "library-samples" / "datasets" / "trip-planner" / "benchmark.yaml"
    dataset = Dataset.from_yaml(path)
    assert len(dataset) == 250
    assert dataset[0].prompt
