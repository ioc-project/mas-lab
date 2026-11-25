#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The provider by feature matrix in docs/manifests/llm-providers.md equals the ``features`` in library.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_VOCABULARY = ["tools", "streaming", "structured_output", "prompt_cache", "thinking", "vision"]


def _library_features() -> dict[str, list[str]]:
    data = yaml.safe_load((_ROOT / "library-standard" / "library.yaml").read_text(encoding="utf-8"))
    rows = data.get("plugins") or data.get("entries") or data
    out: dict[str, list[str]] = {}
    for row in _rows(rows):
        attrs = row.get("attributes") or {}
        # an LLM provider layer: a protocol layer that serves model calls only (the identity layer also serves tools)
        if row.get("type") == "layer" and attrs.get("protocol_layer") and attrs.get("call_kinds") == ["llm"]:
            out[str(row["name"])] = sorted(attrs.get("features") or [])
    return out


def _rows(node: object) -> list[dict]:
    if isinstance(node, list):
        return [item for item in node if isinstance(item, dict)]
    if isinstance(node, dict):
        for value in node.values():
            if isinstance(value, list) and value and isinstance(value[0], dict) and "type" in value[0]:
                return value
    return []


def _doc_matrix() -> dict[str, list[str]]:
    lines = (_ROOT / "docs" / "manifests" / "llm-providers.md").read_text(encoding="utf-8").splitlines()
    header = next(i for i, line in enumerate(lines) if line.startswith("| Provider |"))
    columns = [cell.strip() for cell in lines[header].strip("|").split("|")][1:]
    assert columns == _VOCABULARY
    out: dict[str, list[str]] = {}
    for line in lines[header + 2 :]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        assert len(cells) == len(columns) + 1, line
        out[cells[0]] = sorted(name for name, cell in zip(columns, cells[1:], strict=True) if cell == "yes")
    return out


def test_every_protocol_layer_lists_features_from_the_fixed_vocabulary() -> None:
    features = _library_features()
    assert len(features) >= 15
    for name, listed in features.items():
        assert listed, f"{name} has no features"
        assert set(listed) <= set(_VOCABULARY), name


def test_doc_matrix_equals_library_features() -> None:
    assert _doc_matrix() == _library_features()
