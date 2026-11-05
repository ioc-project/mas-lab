#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Migration script must keep extra user turns, matching the runtime loader."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml


def _mod():
    path = Path(__file__).resolve().parents[4] / "scripts" / "migrate_dataset_envelope.py"
    spec = importlib.util.spec_from_file_location("migrate_dataset_envelope", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_migrate_keeps_extra_user_turns_and_hitl():
    migrate = _mod()
    out = migrate._migrate_item(
        {
            "id": "q1",
            "prompt": "First",
            "turns": [
                {"role": "user", "content": "Second"},
                {"role": "hitl", "content": "Approve"},
            ],
        }
    )
    assert out["inputs"]["user"] == ["First", "Second"]
    assert out["inputs"]["hitl"] == ["Approve"]


def test_migrate_moves_app_expectations_under_details():
    out = _mod()._migrate_item(
        {
            "id": "q1",
            "inputs": {"user": "Q"},
            "expectations": {"ground_truth": "a", "verdict": {"action": "x"}, "details": {"k": 1}},
        }
    )
    assert out["expectations"] == {"ground_truth": "a", "details": {"k": 1, "verdict": {"action": "x"}}}


def test_migrate_rejects_details_key_clash():
    with pytest.raises(ValueError, match="already exist in details"):
        _mod()._migrate_item({"id": "q1", "inputs": {"user": "Q"}, "expectations": {"k": 2, "details": {"k": 1}}})


def test_migrate_collapses_single_pointer_tool_fixtures():
    migrate = _mod()
    out = migrate._migrate_item({"id": "q1", "inputs": {"user": "Q", "tool_fixtures": {"scene": "f.yaml"}}})
    assert out["inputs"]["tool_fixtures"] == "f.yaml"
    kept = {"by_tool": {"*": "f.yaml"}}
    assert (
        migrate._migrate_item({"id": "q", "inputs": {"user": "Q", "tool_fixtures": kept}})["inputs"]["tool_fixtures"]
        == kept
    )
    with pytest.raises(ValueError, match="not generic"):
        migrate._migrate_item({"id": "q", "inputs": {"user": "Q", "tool_fixtures": {"a": "x", "b": "y"}}})


def test_migrate_paths_rewrites_only_datasets(tmp_path: Path):
    ds = tmp_path / "d" / "dataset.yaml"
    ds.parent.mkdir()
    ds.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "lab/v1",
                "kind": "Dataset",
                "metadata": {"name": "d"},
                "spec": {
                    "item_schema": {"id": "string"},
                    "items": [{"id": "a", "inputs": {"user": "Q"}, "expectations": {"verdict": 1}}],
                },
            }
        ),
        encoding="utf-8",
    )
    other = tmp_path / "d" / "other.yaml"
    other.write_text("items:\n  - {prompt: keep}\n", encoding="utf-8")

    assert _mod().main([str(tmp_path)]) == 0

    spec = yaml.safe_load(ds.read_text(encoding="utf-8"))["spec"]
    assert "item_schema" not in spec
    assert spec["items"][0]["expectations"] == {"details": {"verdict": 1}}
    assert other.read_text(encoding="utf-8") == "items:\n  - {prompt: keep}\n"
    assert not ds.read_text(encoding="utf-8").startswith("#")


def test_migrate_refuses_to_drop_body_comments(tmp_path: Path, capsys):
    ds = tmp_path / "dataset.yaml"
    ds.write_text(
        "# leading comment is kept\n"
        "apiVersion: lab/v1\nkind: Dataset\nmetadata: {name: d}\nspec:\n  items:\n"
        "  - id: a\n    inputs: {user: Q}\n    expectations: {verdict: 1}\n"
        "  # - id: b  (disabled item)\n"
        "  - id: c\n    inputs:\n      user: |\n        # not a comment, part of the prompt\n",
        encoding="utf-8",
    )
    before = ds.read_text(encoding="utf-8")
    assert _mod().main([str(ds)]) == 1
    assert "comments on lines [10]" in capsys.readouterr().err
    assert ds.read_text(encoding="utf-8") == before
