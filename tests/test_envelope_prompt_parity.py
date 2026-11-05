#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Verify envelope migration preserved dataset primary prompts vs git HEAD."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def _dataset_items(doc: dict | list | None) -> list[dict]:
    if not doc:
        return []
    if isinstance(doc, list):
        return doc
    return doc.get("spec", {}).get("items") or doc.get("items") or []


def _primary_prompt(item: dict) -> str:
    if "inputs" in item:
        user = item["inputs"].get("user")
        if isinstance(user, str):
            return user.strip()
        if isinstance(user, list) and user:
            first = user[0]
            if isinstance(first, dict):
                return str(first.get("content", "")).strip()
            return str(first).strip()
        return ""
    prompt = item.get("prompt", "")
    if prompt:
        return str(prompt).strip()
    turns = item.get("turns") or []
    if turns:
        return str(turns[0].get("content", "")).strip()
    return ""


@pytest.mark.parametrize(
    "rel_path",
    [
        "tests/fixtures/lab-smoke/dataset.yaml",
        "library-samples/datasets/trip-planner/benchmark.yaml",
        "docs/tutorials/03-experiments-and-analysis/dataset.yaml",
    ],
)
def test_envelope_migration_preserves_primary_prompt(rel_path: str) -> None:
    """Each migrated item's first user message matches the pre-migration prompt."""
    current = REPO_ROOT / rel_path
    if not current.is_file():
        pytest.skip(f"{rel_path} missing")

    try:
        old_text = subprocess.check_output(["git", "show", f"HEAD:{rel_path}"], text=True, cwd=REPO_ROOT)
    except subprocess.CalledProcessError:
        pytest.skip(f"no HEAD version for {rel_path}")

    if rel_path.endswith(".json"):
        old_doc = json.loads(old_text)
        new_doc = json.loads(current.read_text(encoding="utf-8"))
    else:
        old_doc = yaml.safe_load(old_text)
        new_doc = yaml.safe_load(current.read_text(encoding="utf-8"))

    old_by_id = {str(i["id"]): i for i in _dataset_items(old_doc)}
    new_by_id = {str(i["id"]): i for i in _dataset_items(new_doc)}

    assert old_by_id, f"no items in HEAD version of {rel_path}"
    for iid, old_item in old_by_id.items():
        assert iid in new_by_id, f"{rel_path}: item {iid} missing after migration"
        assert _primary_prompt(old_item) == _primary_prompt(new_by_id[iid]), (
            f"{rel_path} item {iid}: primary prompt changed"
        )
