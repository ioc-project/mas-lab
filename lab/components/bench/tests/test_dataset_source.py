#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Unit tests for Dataset spec.source materialization."""

from __future__ import annotations

import csv
import json
import pickle
from pathlib import Path

import pytest
import yaml
from mas.lab.benchmark.dataset import Dataset
from mas.lab.benchmark.dataset_source import materialize_source
from mas.lab.schemas.validate import validate_against_lab_schema
from mas.runtime.spec.source import load_yaml_file


def test_jsonl_mmlu_map(tmp_path: Path):
    (tmp_path / "rows.jsonl").write_text(
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
    items = materialize_source(
        {
            "kind": "jsonl",
            "path": "rows.jsonl",
            "map": {
                "id": "question_id",
                "inputs.user": "{question}\n{options_text}",
                "expectations.ground_truth": "answer_index",
            },
        },
        base_path=tmp_path,
    )
    assert items[0]["id"] == "q1"
    assert "B. 4" in items[0]["inputs"]["user"]
    assert items[0]["expectations"]["ground_truth"] == 1


def test_csv_source(tmp_path: Path):
    with (tmp_path / "q.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["id", "question", "answer"])
        writer.writeheader()
        writer.writerow({"id": "1", "question": "Capital?", "answer": "Paris"})
    items = materialize_source(
        {
            "kind": "csv",
            "path": "q.csv",
            "map": {
                "id": "id",
                "inputs.user": "question",
                "expectations.ground_truth": "answer",
            },
        },
        base_path=tmp_path,
    )
    assert items[0]["inputs"]["user"] == "Capital?"
    assert items[0]["expectations"]["ground_truth"] == "Paris"


def test_glob_yaml_source(tmp_path: Path):
    shard = tmp_path / "shards"
    shard.mkdir()
    (shard / "a.yaml").write_text("id: a\nquestion: A?\nanswer: 1\n", encoding="utf-8")
    (shard / "b.yaml").write_text("id: b\nquestion: B?\nanswer: 2\n", encoding="utf-8")
    items = materialize_source(
        {
            "kind": "glob",
            "path": "shards/*.yaml",
            "map": {
                "id": "id",
                "inputs.user": "question",
                "expectations.ground_truth": "answer",
            },
        },
        base_path=tmp_path,
    )
    assert [i["id"] for i in items] == ["a", "b"]


def test_pickle_source(tmp_path: Path):
    payload = [{"question_id": "p1", "question": "Q?", "answer_index": 0}]
    (tmp_path / "q.pkl").write_bytes(pickle.dumps(payload))
    items = materialize_source(
        {
            "kind": "pickle",
            "path": "q.pkl",
            "map": {
                "id": "question_id",
                "inputs.user": "question",
                "expectations.ground_truth": "answer_index",
            },
        },
        base_path=tmp_path,
    )
    assert items[0]["id"] == "p1"
    assert items[0]["inputs"]["user"] == "Q?"


def test_limit_and_template_object(tmp_path: Path):
    lines = [json.dumps({"id": str(i), "question": f"Q{i}"}) + "\n" for i in range(5)]
    (tmp_path / "rows.jsonl").write_text("".join(lines), encoding="utf-8")
    items = materialize_source(
        {
            "kind": "jsonl",
            "path": "rows.jsonl",
            "limit": 2,
            "map": {
                "id": "id",
                "inputs.user": {"template": "Ask: {question}"},
            },
        },
        base_path=tmp_path,
    )
    assert len(items) == 2
    assert items[0]["inputs"]["user"] == "Ask: Q0"


def test_unknown_kind(tmp_path: Path):
    with pytest.raises(ValueError, match="unknown spec.source.kind"):
        materialize_source({"kind": "parquet"}, base_path=tmp_path)


def test_huggingface_kind_maps_without_hub(monkeypatch, tmp_path: Path):
    def fake_hf(source):
        assert source["id"] == "TIGER-Lab/MMLU-Pro"
        assert source.get("split") == "validation"
        return [
            {
                "question_id": "hf1",
                "question": "2+2?",
                "options": ["3", "4"],
                "answer_index": 1,
            }
        ]

    monkeypatch.setattr(
        "mas.lab.benchmark.dataset_source._load_huggingface",
        fake_hf,
    )
    items = materialize_source(
        {
            "kind": "huggingface",
            "id": "TIGER-Lab/MMLU-Pro",
            "split": "validation",
            "map": {
                "id": "question_id",
                "inputs.user": "{question}\n{options_text}",
                "expectations.ground_truth": "answer_index",
            },
        },
        base_path=tmp_path,
    )
    assert items[0]["id"] == "hf1"
    assert "B. 4" in items[0]["inputs"]["user"]


def test_load_huggingface_passes_id_and_split(monkeypatch):
    import sys
    import types

    called: dict = {}

    fake = types.ModuleType("datasets")

    def load_dataset(ds_id, *args, split="validation", **kwargs):
        called["id"] = ds_id
        called["split"] = split
        called["config"] = args[0] if args else kwargs.get("name")
        return [{"question": "x", "question_id": "1"}]

    fake.load_dataset = load_dataset
    monkeypatch.setitem(sys.modules, "datasets", fake)
    from mas.lab.benchmark.dataset_source import _load_huggingface

    rows = _load_huggingface({"kind": "huggingface", "id": "TIGER-Lab/MMLU-Pro", "split": "test", "config": "default"})
    assert called["id"] == "TIGER-Lab/MMLU-Pro"
    assert called["split"] == "test"
    assert called["config"] == "default"
    assert rows[0]["question"] == "x"


def test_huggingface_missing_id():
    from mas.lab.benchmark.dataset_source import _load_huggingface

    with pytest.raises(ValueError, match="huggingface source needs id"):
        _load_huggingface({"kind": "huggingface", "split": "test"})


def test_experiment_source_overlay(tmp_path: Path):
    (tmp_path / "rows.jsonl").write_text(
        json.dumps({"id": "a", "question": "A"})
        + "\n"
        + json.dumps({"id": "b", "question": "B"})
        + "\n"
        + json.dumps({"id": "c", "question": "C"})
        + "\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "dataset.yaml"
    yaml.dump(
        {
            "apiVersion": "lab/v1",
            "kind": "Dataset",
            "metadata": {"name": "mmlu", "version": "v1"},
            "spec": {
                "source": {
                    "kind": "jsonl",
                    "path": "rows.jsonl",
                    "map": {"id": "id", "inputs.user": "question"},
                }
            },
        },
        manifest.open("w"),
    )
    ds = Dataset.from_yaml(manifest, source_overlay={"limit": 1})
    assert len(ds) == 1
    assert ds[0].id == "a"


def test_huggingface_missing_package_or_id(monkeypatch, tmp_path: Path):
    import builtins
    import sys

    monkeypatch.delitem(sys.modules, "datasets", raising=False)
    real_import = builtins.__import__

    def _blocked(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "datasets" or name.startswith("datasets."):
            raise ImportError("blocked")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    with pytest.raises(ImportError, match="datasets"):
        materialize_source(
            {"kind": "huggingface", "id": "TIGER-Lab/MMLU-Pro"},
            base_path=tmp_path,
        )


def test_checked_in_mmlu_jsonl_example():
    path = Path(__file__).resolve().parents[4] / "docs" / "schemas" / "examples" / "datasets" / "mmlu-pro-jsonl.yaml"
    data = load_yaml_file(path)
    validate_against_lab_schema("dataset", data)
    ds = Dataset.from_yaml(path)
    assert len(ds) == 2
    assert ds[0].id == "q1"
    assert "B. 4" in ds[0].prompt
    assert ds[0].run_input.expectations["ground_truth"] == 1
    assert ds[1].id == "q2"
    assert ds[0].metadata.get("category") == "math"


def test_huggingface_example_validates():
    path = Path(__file__).resolve().parents[4] / "docs" / "schemas" / "examples" / "datasets" / "mmlu-pro.yaml"
    data = load_yaml_file(path)
    validate_against_lab_schema("dataset", data)
    assert data["spec"]["source"]["id"] == "TIGER-Lab/MMLU-Pro"


def test_source_then_inline_items(tmp_path: Path):
    (tmp_path / "rows.jsonl").write_text(
        json.dumps({"id": "s1", "question": "from source"}) + "\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "dataset.yaml"
    yaml.dump(
        {
            "apiVersion": "lab/v1",
            "kind": "Dataset",
            "metadata": {"name": "mix", "version": "v1"},
            "spec": {
                "source": {
                    "kind": "jsonl",
                    "path": "rows.jsonl",
                    "map": {"id": "id", "inputs.user": "question"},
                },
                "items": [{"id": "local", "inputs": {"user": "inline"}}],
            },
        },
        manifest.open("w"),
    )
    ds = Dataset.from_yaml(manifest)
    assert [i.id for i in ds] == ["s1", "local"]


@pytest.mark.skipif(
    __import__("os").environ.get("MAS_HF_LIVE") != "1",
    reason="live HuggingFace download is opt-in (MAS_HF_LIVE=1)",
)
def test_huggingface_live_mmlu_pro_one_row():
    pytest.importorskip("datasets")
    items = materialize_source(
        {
            "kind": "huggingface",
            "id": "TIGER-Lab/MMLU-Pro",
            "split": "validation[:1]",
            "map": {
                "id": "question_id",
                "inputs.user": "{question}\n{options_text}",
                "expectations.ground_truth": "answer_index",
            },
        },
        base_path=Path("."),
    )
    assert items[0]["id"]
    assert items[0]["inputs"]["user"]
    assert items[0]["expectations"]["ground_truth"] is not None
