#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

from mas.ctl.paths import resolve_manifest


def test_resolve_manifest_library_app(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "example-library"
    app = root / "apps" / "trip-planner" / "v2"
    app.mkdir(parents=True)
    (root / "library.yaml").write_text(
        "apiVersion: mas/v1\nkind: Library\nname: mas-example-library\napps:\n  trip-planner: apps/trip-planner\n",
        encoding="utf-8",
    )
    mas = app / "mas.yaml"
    mas.write_text(
        "apiVersion: mas/v1\nkind: MAS\nmetadata:\n  name: trip-planner\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MAS_LIBRARY_PATHS", str(root))

    resolved = resolve_manifest("example-library:trip-planner@v2", cwd=tmp_path)
    assert resolved == mas.resolve()
    assert resolve_manifest("example-library:apps/trip-planner/v2", cwd=tmp_path) == mas.resolve()
