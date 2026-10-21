#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Registry indexes overlays by spec.target independently of apps."""

from __future__ import annotations

from pathlib import Path

from mas.lab.controller.lab_registry import LabRegistry, infer_overlay_target


def test_infer_empty_mas_name_from_app_overlays_path(tmp_path: Path):
    lib = tmp_path / "lib"
    path = lib / "apps" / "plain" / "overlays" / "layout.yaml"
    doc = {
        "kind": "Overlay",
        "spec": {"target": {"kind": "MAS"}, "patch": {}},
        "x-canvas-positions": {"n": {"x": 1, "y": 2}},
    }
    assert infer_overlay_target(path, doc, lib_dir=lib) == ("MAS", "plain")


def test_infer_versioned_app_overlay_path(tmp_path: Path):
    lib = tmp_path / "lib"
    path = lib / "apps" / "triage" / "v1" / "overlays" / "layout.yaml"
    doc = {"kind": "Overlay", "spec": {"target": {"kind": "MAS"}}}
    assert infer_overlay_target(path, doc, lib_dir=lib) == ("MAS", "triage")


def test_library_overlay_without_name_does_not_bind(tmp_path: Path):
    lib = tmp_path / "lib"
    path = lib / "overlays" / "layout.yaml"
    doc = {"kind": "Overlay", "spec": {"target": {"kind": "MAS"}, "patch": {}}}
    assert infer_overlay_target(path, doc, lib_dir=lib) is None


def test_overlay_records_register_layout_before_app_exists(tmp_path: Path):
    lib = tmp_path / "lib"
    overlays = lib / "overlays"
    overlays.mkdir(parents=True)
    (overlays / "ghost-canvas.yaml").write_text(
        "\n".join(
            [
                "apiVersion: mas/v1",
                "kind: Overlay",
                "metadata: {name: ghost}",
                "spec:",
                "  target: {kind: MAS, name: ghost}",
                "  patch: {}",
                "x-canvas-positions:",
                "  n: {x: 1, y: 2}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    matched = LabRegistry.layout_overlays_in_directory(lib, kind="MAS", name="ghost")
    assert len(matched) == 1
    assert matched[0][0].name == "ghost-canvas.yaml"
    assert not (lib / "apps" / "ghost").exists()
