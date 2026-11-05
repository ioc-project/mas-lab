#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""library:app identifier resolution (name@version catalog ids)."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.apps import AppNotFoundError, get_app
from mas.library_catalog import (
    UnpinnedCatalogIdError,
    apps_in_library,
    parse_library_ref,
    parse_versioned_id,
    require_pinned_catalog_id,
    versioned_id,
)
from mas.library_roots import resolve_named_library_root
from mas.runtime.package_refs import resolve_path_ref


def _write_versioned_library(tmp_path: Path) -> Path:
    root = tmp_path / "example-library"
    for ver in ("v1", "v2"):
        app = root / "apps" / "route-planner" / ver
        app.mkdir(parents=True)
        (app / "mas.yaml").write_text(
            "apiVersion: mas/v1\nkind: MAS\nmetadata:\n  name: route-planner\n",
            encoding="utf-8",
        )
        ds = app / "datasets" / "queries"
        ds.mkdir(parents=True)
        (ds / "celestia-weekend.yaml").write_text("id: celestia-weekend\n")
    scenarios = root / "apps" / "route-planner" / "v1" / "datasets" / "scenarios"
    scenarios.mkdir(parents=True)
    (scenarios / "dataset.yaml").write_text(
        "apiVersion: lab/v1\nkind: Dataset\n"
        "metadata:\n  name: route-planner-scenarios\n  version: v1\n"
        "spec:\n  app: route-planner@^v1\n",
        encoding="utf-8",
    )
    queries = root / "apps" / "route-planner" / "v2" / "datasets" / "queries"
    queries.mkdir(parents=True, exist_ok=True)
    (queries / "dataset.yaml").write_text(
        "apiVersion: lab/v1\nkind: Dataset\n"
        "metadata:\n  name: route-planner-queries\n  version: v2\n"
        "spec:\n  app: route-planner@^v2\n",
        encoding="utf-8",
    )
    tf = queries / "tool_fixtures"
    tf.mkdir()
    (tf / "celestia-weekend.yaml").write_text("id: celestia-weekend\n", encoding="utf-8")
    (root / "library.yaml").write_text(
        "apiVersion: mas/v1\n"
        "kind: Library\n"
        "name: mas-example-library\n"
        "apps:\n"
        "  route-planner: apps/route-planner\n"
        "datasets:\n"
        "  route-planner-scenarios@v1: apps/route-planner/v1/datasets/scenarios\n"
        "  route-planner-queries@v2: apps/route-planner/v2/datasets/queries\n",
        encoding="utf-8",
    )
    return root


def test_parse_library_ref() -> None:
    assert parse_library_ref("example-library:route-planner@v2") == (
        "example-library",
        "route-planner@v2",
    )
    assert parse_library_ref("example-library:apps/route-planner/v2") == (
        "example-library",
        "apps/route-planner/v2",
    )
    assert parse_library_ref("../../apps/route-planner/mas.yaml") is None
    assert parse_library_ref("/abs/path") is None


def test_parse_versioned_id() -> None:
    assert parse_versioned_id("route-planner@v2") == ("route-planner", "v2")
    assert parse_versioned_id("route-planner") == ("route-planner", None)
    assert parse_versioned_id("route-planner@latest") == ("route-planner", None)
    assert parse_versioned_id("route-planner@2") == ("route-planner", "v2")
    assert versioned_id("route-planner", "v2") == "route-planner@v2"
    assert versioned_id("route-planner", None) == "route-planner"


def test_get_app_library_qualified_and_latest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _write_versioned_library(tmp_path)
    monkeypatch.setenv("MAS_LIBRARY_PATHS", str(root))

    v1 = (root / "apps" / "route-planner" / "v1").resolve()
    v2 = (root / "apps" / "route-planner" / "v2").resolve()
    assert get_app("example-library:route-planner@v2") == v2
    assert get_app("example-library:route-planner@v1") == v1
    assert get_app("route-planner@v2") == v2
    assert get_app("example-library:route-planner") == v2
    assert get_app("route-planner") == v2


def test_require_pinned_catalog_id_for_versioned_family(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _write_versioned_library(tmp_path)
    monkeypatch.setenv("MAS_LIBRARY_PATHS", str(root))
    require_pinned_catalog_id("example-library:route-planner@v1", kind="app")
    require_pinned_catalog_id("example-library:apps/route-planner/v1/mas.yaml", kind="app")
    with pytest.raises(UnpinnedCatalogIdError, match="@v1"):
        require_pinned_catalog_id("example-library:route-planner", kind="app")
    with pytest.raises(UnpinnedCatalogIdError):
        require_pinned_catalog_id("route-planner", kind="app")
    with pytest.raises(UnpinnedCatalogIdError):
        require_pinned_catalog_id("route-planner-scenarios", kind="dataset")
    require_pinned_catalog_id("route-planner-scenarios@v1", kind="dataset")


def test_get_app_rejects_hyphen_and_slash_aliases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _write_versioned_library(tmp_path)
    monkeypatch.setenv("MAS_LIBRARY_PATHS", str(root))
    with pytest.raises(AppNotFoundError, match="not found"):
        get_app("route-planner-v2")
    with pytest.raises(AppNotFoundError, match="not found"):
        get_app("example-library:route-planner/v2")


def test_get_app_unknown_library_app_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _write_versioned_library(tmp_path)
    monkeypatch.setenv("MAS_LIBRARY_PATHS", str(root))
    with pytest.raises(AppNotFoundError, match="not found"):
        get_app("example-library:does-not-exist")


def test_resolve_path_ref_catalog_id_and_library_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _write_versioned_library(tmp_path)
    monkeypatch.setenv("MAS_LIBRARY_PATHS", str(root))
    mas = resolve_path_ref("example-library:route-planner@v2", tmp_path)
    assert mas.name == "mas.yaml"
    assert mas.is_file()
    unqualified = resolve_path_ref("route-planner@v2", tmp_path)
    assert unqualified == mas
    ds = resolve_path_ref("example-library:route-planner-scenarios@v1", tmp_path)
    assert ds.name == "dataset.yaml"
    assert ds.parent.name == "scenarios"
    assert "v1" in ds.parts
    path_ref = resolve_path_ref("example-library:apps/route-planner/v2", tmp_path)
    assert path_ref.is_dir()
    assert path_ref.name == "v2"


def test_resolve_path_ref_catalog_relative_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _write_versioned_library(tmp_path)
    monkeypatch.setenv("MAS_LIBRARY_PATHS", str(root))
    fixture = resolve_path_ref(
        "example-library:route-planner-queries@v2/tool_fixtures/celestia-weekend.yaml",
        tmp_path,
    )
    assert fixture.is_file()
    assert fixture.parent.name == "tool_fixtures"
    assert "v2" in fixture.parts
    via_app = resolve_path_ref(
        "example-library:route-planner@v2/datasets/queries/tool_fixtures/celestia-weekend.yaml",
        tmp_path,
    )
    assert via_app == fixture
    unqualified = resolve_path_ref(
        "route-planner-queries@v2/tool_fixtures/celestia-weekend.yaml",
        tmp_path,
    )
    assert unqualified == fixture
    # Library-root paths still win over treating the first segment as an id.
    apps = resolve_path_ref("example-library:apps/route-planner/v2", tmp_path)
    assert apps.is_dir() and apps.name == "v2"


def test_library_name_is_directory_stem_not_an_alias(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _write_versioned_library(tmp_path)
    monkeypatch.setenv("MAS_LIBRARY_PATHS", str(root))
    assert resolve_named_library_root("example-library") == root.resolve()
    assert resolve_named_library_root("mas-example-library") == root.resolve()
    assert resolve_named_library_root("example") is None


def test_apps_in_library_uses_at_version(tmp_path: Path) -> None:
    root = _write_versioned_library(tmp_path)
    found = apps_in_library(root)
    assert "route-planner@v1" in found
    assert "route-planner@v2" in found
    assert "route-planner" not in found
    assert "route-planner-v2" not in found
    assert "route-planner/v2" not in found


def test_dataset_lives_under_app_version(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    from mas.library_catalog import ensure_dataset_supports_app, resolve_id_in_library

    root = _write_versioned_library(tmp_path)
    scenarios = resolve_id_in_library(root, "route-planner-scenarios@v1", kind="dataset")
    queries = resolve_id_in_library(root, "route-planner-queries@v2", kind="dataset")
    assert scenarios is not None and queries is not None
    assert scenarios.name == "dataset.yaml"
    assert "v1" in scenarios.parts
    assert "v2" in queries.parts
    latest = resolve_id_in_library(root, "route-planner-scenarios", kind="dataset")
    assert latest == scenarios
    v1 = root / "apps" / "route-planner" / "v1"
    v2 = root / "apps" / "route-planner" / "v2"
    ensure_dataset_supports_app(scenarios, v1)
    with caplog.at_level(logging.WARNING, logger="mas.library_catalog"):
        ensure_dataset_supports_app(scenarios, v2)
    assert "does not include app" in caplog.text
    caplog.clear()
    ensure_dataset_supports_app(queries, v2)
    with caplog.at_level(logging.WARNING, logger="mas.library_catalog"):
        ensure_dataset_supports_app(queries, v1)
    assert "does not include app" in caplog.text
