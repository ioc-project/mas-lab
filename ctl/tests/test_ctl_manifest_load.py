#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for mas.runtime.spec.source manifest loading."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from mas.runtime.spec.source import (
    load_yaml_mapping,
    resolve_manifest_source,
    resolve_ref_with_search,
)


def test_load_yaml_mapping(tmp_path: Path) -> None:
    p = tmp_path / "mas.yaml"
    yaml.safe_dump({"kind": "MAS", "metadata": {"name": "x"}}, p.open("w"))
    data = load_yaml_mapping(p)
    assert data["metadata"]["name"] == "x"


def test_resolve_manifest_source_inline() -> None:
    inline = {"kind": "Agent", "spec": {}}
    assert resolve_manifest_source(inline) == inline


def test_resolve_manifest_source_ref(tmp_path: Path) -> None:
    f = tmp_path / "agent.yaml"
    f.write_text("kind: Agent\n", encoding="utf-8")
    data = resolve_manifest_source("agent.yaml", anchor=tmp_path)
    assert data["kind"] == "Agent"


def test_load_yaml_mapping_rejects_list(tmp_path: Path) -> None:
    f = tmp_path / "bad.yaml"
    f.write_text("- item\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_yaml_mapping(f)


def test_resolve_ref_with_search(tmp_path: Path) -> None:
    sub = tmp_path / "infra"
    sub.mkdir()
    f = sub / "local.yaml"
    f.write_text("kind: Infra\n", encoding="utf-8")
    path = resolve_ref_with_search("local", tmp_path, search_dirs=[sub])
    assert path == f.resolve()
