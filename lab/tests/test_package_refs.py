#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

from mas.apps import get_app
from mas.lab.lab.config import MASSpec
from mas.runtime.package_refs import resolve_path_ref


def test_trip_planner_manifest_via_app_registry() -> None:
    path = get_app("trip-planner") / "mas.yaml"
    assert path.is_file()
    assert path.name == "mas.yaml"
    assert path.parent.name == "trip-planner"


def test_resolve_path_ref_manifest_library_scheme() -> None:
    path = resolve_path_ref("samples:apps/trip-planner/mas.yaml", Path.cwd())
    assert path.is_file()
    assert path.name == "mas.yaml"
    assert "trip-planner" in str(path)
    assert "trip-planner-linear" not in str(path)


def test_masspec_from_dict_supports_app_locator() -> None:
    spec = MASSpec.from_dict(
        {"app": "trip-planner", "base_scenario": "baseline"},
        Path.cwd(),
    )
    assert spec.manifest is not None
    assert spec.manifest.is_file()
    assert spec.manifest.parent.name == "trip-planner"


def test_masspec_from_dict_library_app_string(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "example-library"
    app = root / "apps" / "trip-planner" / "v2"
    app.mkdir(parents=True)
    (root / "library.yaml").write_text(
        "apiVersion: mas/v1\nkind: Library\nname: mas-example-library\napps:\n  trip-planner: apps/trip-planner\n",
        encoding="utf-8",
    )
    (app / "mas.yaml").write_text(
        "apiVersion: mas/v1\nkind: MAS\nmetadata:\n  name: trip-planner\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MAS_LIBRARY_PATHS", str(root))

    spec = MASSpec.from_dict("example-library:trip-planner@v2", tmp_path)
    assert spec.manifest is not None
    assert spec.manifest.is_file()
    assert spec.manifest.parent.name == "v2"

    via_path = MASSpec.from_dict(
        {"manifest": "example-library:apps/trip-planner/v2"},
        tmp_path,
    )
    assert via_path.manifest == spec.manifest
