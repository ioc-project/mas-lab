#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Coverage for apps/datasets/tools discovery and error paths in
mas.library_catalog (the filesystem discovery layer the registry refactor
leans on)."""

from __future__ import annotations

from pathlib import Path

import mas.library_catalog as library_catalog
import pytest
from mas.library_catalog import (
    _dataset_keys,
    _is_app_directory,
    _load_library_manifest,
    _resolve_manifest_entry,
    discover_apps,
    discover_datasets,
    discover_tools,
    find_tool_manifest,
)


def test_load_library_manifest_missing_returns_empty(tmp_path: Path) -> None:
    assert _load_library_manifest(tmp_path) == {}


def test_load_library_manifest_parse_error_raises(tmp_path: Path) -> None:
    (tmp_path / "library.yaml").write_text("::: not: valid: yaml: [", encoding="utf-8")
    # A present-but-malformed manifest is a hard config error, not a silent {}.
    with pytest.raises(ValueError, match="Failed to parse library manifest"):
        _load_library_manifest(tmp_path)


def test_load_library_manifest_non_mapping_raises(tmp_path: Path) -> None:
    (tmp_path / "library.yaml").write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a mapping"):
        _load_library_manifest(tmp_path)


def test_resolve_manifest_entry_rejects_blank(tmp_path: Path) -> None:
    assert _resolve_manifest_entry(tmp_path, "") is None
    assert _resolve_manifest_entry(tmp_path, "   ") is None
    assert _resolve_manifest_entry(tmp_path, 123) is None  # type: ignore[arg-type]


def test_resolve_manifest_entry_missing_target(tmp_path: Path) -> None:
    assert _resolve_manifest_entry(tmp_path, "nope") is None


def test_is_app_directory_variants(tmp_path: Path) -> None:
    agent_app = tmp_path / "agent_app"
    agent_app.mkdir()
    (agent_app / "agent.yaml").write_text("x", encoding="utf-8")
    assert _is_app_directory(agent_app) is True

    agents_app = tmp_path / "agents_app"
    (agents_app / "agents").mkdir(parents=True)
    (agents_app / "agents" / "one.yaml").write_text("x", encoding="utf-8")
    assert _is_app_directory(agents_app) is True

    empty = tmp_path / "empty"
    empty.mkdir()
    assert _is_app_directory(empty) is False


def test_dataset_keys_parse_error_still_yields_stem(tmp_path: Path) -> None:
    ds_dir = tmp_path / "datasets"
    ds_dir.mkdir()
    bad = ds_dir / "broken.yaml"
    bad.write_text("::: [", encoding="utf-8")
    keys = _dataset_keys(bad, ds_dir)
    assert "broken" in keys


def test_dataset_keys_includes_metadata_name_and_parent(tmp_path: Path) -> None:
    ds_dir = tmp_path / "datasets"
    nested = ds_dir / "group"
    nested.mkdir(parents=True)
    ds = nested / "thing.yaml"
    ds.write_text("metadata:\n  name: my-dataset\n", encoding="utf-8")
    keys = _dataset_keys(ds, ds_dir)
    assert "my-dataset" in keys
    assert "group-thing" in keys
    assert "thing" in keys


def test_discover_apps_manifest_and_scan(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "lib"
    # manifest-declared app
    declared = root / "custom" / "declared_app"
    declared.mkdir(parents=True)
    (declared / "mas.yaml").write_text("x", encoding="utf-8")
    # scan-discovered app
    scanned = root / "apps" / "scanned_app"
    scanned.mkdir(parents=True)
    (scanned / "mas.yaml").write_text("x", encoding="utf-8")

    (root / "library.yaml").write_text("apps:\n  declared: custom/declared_app\n  123: bad\n", encoding="utf-8")

    monkeypatch.setattr(library_catalog, "discover_library_roots", lambda: [root])
    apps = discover_apps()
    assert apps["declared"] == declared.resolve()
    assert apps["scanned_app"] == scanned.resolve()


def test_discover_datasets_manifest_skips_non_str_name(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "lib"
    root.mkdir()
    ds = root / "data" / "d.yaml"
    ds.parent.mkdir(parents=True)
    ds.write_text("metadata:\n  name: d\n", encoding="utf-8")
    (root / "library.yaml").write_text("datasets:\n  good: data/d.yaml\n  123: data/d.yaml\n", encoding="utf-8")

    monkeypatch.setattr(library_catalog, "discover_library_roots", lambda: [root])
    datasets = discover_datasets()
    assert datasets["good"] == ds.resolve()


def test_discover_tools_manifest_and_scan(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "lib"
    # manifest-declared tool dir
    declared = root / "custom" / "declared_tool"
    declared.mkdir(parents=True)
    (declared / "a.tool.yaml").write_text("x", encoding="utf-8")
    # scan-discovered tool dir
    scanned = root / "tools" / "scanned_tool"
    scanned.mkdir(parents=True)
    (scanned / "b.tool.yaml").write_text("x", encoding="utf-8")

    (root / "library.yaml").write_text("tools:\n  declared: custom/declared_tool\n  123: bad\n", encoding="utf-8")

    monkeypatch.setattr(library_catalog, "discover_library_roots", lambda: [root])
    tools = discover_tools()
    assert tools["declared"] == declared.resolve()
    assert tools["scanned_tool"] == scanned.resolve()


def test_discover_tools_empty_when_no_tools(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "lib"
    root.mkdir()
    (root / "library.yaml").write_text("name: x\n", encoding="utf-8")
    monkeypatch.setattr(library_catalog, "discover_library_roots", lambda: [root])
    assert discover_tools() == {}


def test_find_tool_manifest_via_library_yaml_map(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "lib"
    (root / "tools").mkdir(parents=True)
    tool = root / "tools" / "web-search.tool.yaml"
    tool.write_text("kind: Tool\nmetadata:\n  name: web-search\n", encoding="utf-8")
    (root / "library.yaml").write_text("tools:\n  web-search: tools/web-search.tool.yaml\n", encoding="utf-8")
    monkeypatch.setattr(library_catalog, "discover_library_roots", lambda: [root])
    assert find_tool_manifest("web-search") == tool.resolve()


def test_find_tool_manifest_via_flat_scan(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "lib"
    (root / "tools").mkdir(parents=True)
    tool = root / "tools" / "calc.tool.yaml"
    tool.write_text("kind: Tool\nmetadata:\n  name: calc\n", encoding="utf-8")
    (root / "library.yaml").write_text("name: x\n", encoding="utf-8")  # no tools: map
    monkeypatch.setattr(library_catalog, "discover_library_roots", lambda: [root])
    assert find_tool_manifest("calc") == tool.resolve()


def test_find_tool_manifest_unknown_returns_none(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "lib"
    root.mkdir()
    (root / "library.yaml").write_text("name: x\n", encoding="utf-8")
    monkeypatch.setattr(library_catalog, "discover_library_roots", lambda: [root])
    assert find_tool_manifest("nope") is None
