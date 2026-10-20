#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""UI canvas overlay convention tests."""

from pathlib import Path

from mas.ctl.overlay.canvas import (
    UI_CANVAS_OVERLAY_NAME,
    apply_ui_canvas_to_yaml,
    extract_ui_canvas_overlay,
    find_ui_canvas_overlay,
    is_ui_canvas_overlay,
    project_ui_canvas,
)
from mas.ctl.overlay.merge import merge_overlay


def test_is_ui_canvas_overlay_by_name():
    assert is_ui_canvas_overlay(
        {"kind": "Overlay", "metadata": {"name": UI_CANVAS_OVERLAY_NAME}, "spec": {"patch": {}}}
    )


def test_is_ui_canvas_overlay_by_marker():
    assert is_ui_canvas_overlay({"kind": "Overlay", "metadata": {"name": "layout"}, "x-ui-canvas": True})


def test_cot_moderator_is_not_a_canvas_overlay():
    assert not is_ui_canvas_overlay(
        {
            "kind": "Overlay",
            "metadata": {"name": "cot-moderator"},
            "spec": {"target": {"kind": "MAS"}, "patch": {"agents": {}}},
            "x-canvas-positions": {"n1": {"x": 1, "y": 2}},
        }
    )


def test_filename_alone_is_not_enough_without_overlay_kind():
    path = Path("overlays/ui-canvas.yaml")
    assert not is_ui_canvas_overlay(
        {"kind": "MAS", "metadata": {"name": "ui-canvas"}},
        path=path,
    )


def test_find_prefers_canonical_filename(tmp_path: Path):
    overlays = tmp_path / "overlays"
    overlays.mkdir()
    (overlays / "ui-canvas.yaml").write_text(
        "apiVersion: mas/v1\nkind: Overlay\nmetadata:\n  name: ui-canvas\nspec:\n  target:\n    kind: MAS\n  patch: {}\n",
        encoding="utf-8",
    )
    (overlays / "other.yaml").write_text(
        "apiVersion: mas/v1\nkind: Overlay\nmetadata:\n  name: other\nx-ui-canvas: true\nspec:\n  patch: {}\n",
        encoding="utf-8",
    )
    found = find_ui_canvas_overlay(tmp_path)
    assert found is not None
    assert found.name == "ui-canvas.yaml"


def test_find_skips_preferred_filename_that_is_not_an_overlay(tmp_path: Path):
    overlays = tmp_path / "overlays"
    overlays.mkdir()
    (overlays / "ui-canvas.yaml").write_text("kind: MAS\nmetadata:\n  name: decoy\n", encoding="utf-8")
    (overlays / "layout.yaml").write_text(
        "kind: Overlay\nmetadata:\n  name: layout\nx-ui-canvas: true\nspec:\n  patch: {}\n",
        encoding="utf-8",
    )
    found = find_ui_canvas_overlay(tmp_path)
    assert found is not None
    assert found.name == "layout.yaml"


def test_apply_projects_positions_and_per_agent_fields(tmp_path: Path):
    overlays = tmp_path / "overlays"
    overlays.mkdir()
    (overlays / "ui-canvas.yaml").write_text(
        "\n".join(
            [
                "apiVersion: mas/v1",
                "kind: Overlay",
                "metadata:",
                "  name: ui-canvas",
                "spec:",
                "  target: {kind: MAS, name: demo}",
                "  patch: {}",
                "x-ui-canvas: true",
                "x-canvas-positions:",
                "  sre: {x: 0, y: -350}",
                "x-canvas-node-ids:",
                "  sre:",
                "    model: node_model",
                "    designPattern: node_dp",
                "x-text-input:",
                "  sre: demo query",
                "",
            ]
        ),
        encoding="utf-8",
    )
    mas_yaml = "apiVersion: mas/v1\nkind: MAS\nmetadata:\n  name: demo\nspec: {}\n"
    agents = {"sre": "apiVersion: mas/v1\nkind: Agent\nmetadata:\n  name: sre\nspec: {}\n"}
    out_mas, out_agents, path = apply_ui_canvas_to_yaml(tmp_path, mas_yaml, agents)
    assert path is not None
    assert "x-canvas-positions" in out_mas
    assert "node_model" in out_agents["sre"]
    assert "node_dp" in out_agents["sre"]
    assert "demo query" in out_agents["sre"]


def test_apply_returns_no_path_when_preferred_file_is_not_canvas(tmp_path: Path):
    overlays = tmp_path / "overlays"
    overlays.mkdir()
    (overlays / "ui-canvas.yaml").write_text("kind: Config\n", encoding="utf-8")
    mas_yaml = "kind: MAS\nmetadata:\n  name: demo\n"
    out_mas, out_agents, path = apply_ui_canvas_to_yaml(tmp_path, mas_yaml, {"sre": "kind: Agent\n"})
    assert path is None
    assert out_mas == mas_yaml
    assert out_agents == {"sre": "kind: Agent\n"}


def test_project_does_not_apply_spec_patch():
    mas = {"kind": "MAS", "spec": {"agency": {"agents": []}}}
    agents = {"sre": {"kind": "Agent", "spec": {}}}
    overlay = {
        "kind": "Overlay",
        "metadata": {"name": "ui-canvas"},
        "spec": {
            "target": {"kind": "MAS"},
            "patch": {"params": {"injected": True}},
        },
        "x-canvas-positions": {"sre": {"x": 1, "y": 2}},
    }
    project_ui_canvas(mas, agents, overlay)
    assert mas["x-canvas-positions"]["sre"]["x"] == 1
    assert mas["spec"].get("params") is None


def test_extract_strips_canvas_fields_into_overlay():
    mas = {"kind": "MAS", "metadata": {"name": "demo"}, "x-canvas-positions": {"sre": {"x": 0, "y": 1}}}
    agents = {
        "sre": {
            "kind": "Agent",
            "metadata": {"name": "sre", "x-node-id": "node_sre"},
            "spec": {"x-text-input": "q"},
            "x-canvas-node-ids": {"model": "node_model"},
        }
    }
    overlay = extract_ui_canvas_overlay(mas, agents, mas_name="demo")
    assert overlay is not None
    assert overlay["metadata"]["name"] == "ui-canvas"
    assert overlay["x-ui-canvas"] is True
    assert "x-canvas-positions" not in mas
    assert "x-canvas-node-ids" not in agents["sre"]
    assert "x-node-id" not in agents["sre"]["metadata"]
    assert "x-text-input" not in agents["sre"]["spec"]
    assert overlay["x-text-input"]["sre"] == "q"


def test_merge_overlay_empty_patch_copies_root_x_fields():
    base = {"apiVersion": "mas/v1", "kind": "MAS", "metadata": {"name": "sre-triage"}, "spec": {}}
    overlay = {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "ui-canvas"},
        "spec": {"target": {"kind": "MAS", "name": "sre-triage"}, "patch": {}},
        "x-canvas-positions": {"sre": {"x": 0, "y": -350}},
        "x-canvas-node-ids": {"sre": {"role": "node_role"}},
    }
    merged = merge_overlay(base, overlay)
    assert merged["x-canvas-positions"] == {"sre": {"x": 0, "y": -350}}
    assert merged["x-canvas-node-ids"]["sre"]["role"] == "node_role"
    assert "x-canvas-positions" not in merged.get("spec", {})


def test_merge_overlay_patch_x_fields_land_on_document_root():
    base = {"apiVersion": "mas/v1", "kind": "MAS", "metadata": {"name": "demo"}, "spec": {"params": {}}}
    overlay = {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "ui-canvas"},
        "spec": {
            "target": {"kind": "MAS"},
            "patch": {"x-canvas-positions": {"sre": {"x": 4, "y": 5}}},
        },
    }
    merged = merge_overlay(base, overlay)
    assert merged["x-canvas-positions"]["sre"]["x"] == 4
    assert "x-canvas-positions" not in merged["spec"]
