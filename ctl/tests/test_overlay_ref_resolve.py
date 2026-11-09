#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

from pathlib import Path

import pytest
from mas.ctl.paths import resolve_overlay_ref_entries


def test_resolve_overlay_ref_entries_registry_id(tmp_path: Path) -> None:
    overlays_dir = tmp_path / "overlays"
    overlays_dir.mkdir()
    (overlays_dir / "tools.yaml").write_text("kind: Overlay\n", encoding="utf-8")
    mas_dir = tmp_path / "app"
    mas_dir.mkdir()
    (mas_dir / "mas.yaml").write_text("kind: mas\n", encoding="utf-8")

    paths = resolve_overlay_ref_entries(
        ["tools"],
        manifest_dir=mas_dir,
        overlays_dir=overlays_dir,
    )
    assert paths == (overlays_dir / "tools.yaml",)


def test_resolve_overlay_ref_entries_ref_dict(tmp_path: Path) -> None:
    base = tmp_path / "lab"
    base.mkdir()
    overlay = base / "patches" / "full.yaml"
    overlay.parent.mkdir()
    overlay.write_text("kind: Overlay\n", encoding="utf-8")
    mas_dir = base / "app"
    mas_dir.mkdir()

    paths = resolve_overlay_ref_entries(
        [{"ref": "patches/full.yaml"}],
        manifest_dir=mas_dir,
        base_dir=base,
    )
    assert paths == (overlay.resolve(),)


def test_resolve_overlay_ref_entries_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        resolve_overlay_ref_entries(
            ["missing"],
            manifest_dir=tmp_path,
            overlays_dir=tmp_path / "overlays",
        )
