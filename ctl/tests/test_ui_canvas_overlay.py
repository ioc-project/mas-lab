#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""UI canvas overlay association tests."""

from pathlib import Path

from mas.ctl.overlay.canvas import (
    apply_ui_canvas_to_overlay_yaml,
    apply_ui_canvas_to_yaml,
    extract_overlay_document_canvas,
    extract_ui_canvas_overlay,
    find_layout_overlay,
    overlay_has_canvas_extensions,
    overlay_is_layout_only,
    overlay_targets_mas,
    persist_overlay_canvas_yaml,
    persist_ui_canvas_yaml,
    project_ui_canvas,
    write_ui_canvas_overlay,
)
from mas.ctl.overlay.merge import merge_overlay


def test_target_binds_overlay_to_mas():
    assert overlay_targets_mas(
        {"kind": "Overlay", "spec": {"target": {"kind": "MAS"}, "patch": {}}},
        mas_name="demo",
    )
    assert overlay_targets_mas(
        {"kind": "Overlay", "spec": {"target": {"kind": "MAS", "name": "demo"}, "patch": {}}},
        mas_name="demo",
    )
    assert not overlay_targets_mas(
        {"kind": "Overlay", "spec": {"target": {"kind": "MAS", "name": "other"}, "patch": {}}},
        mas_name="demo",
    )
    assert not overlay_targets_mas(
        {"kind": "Overlay", "spec": {"target": {"kind": "Agent"}, "patch": {}}},
        mas_name="demo",
    )
    assert not overlay_targets_mas(
        {"kind": "Overlay", "spec": {"target": {"kind": "MAS"}, "patch": {}}},
        mas_name="demo",
        require_name=True,
    )


def test_spec_overlay_with_canvas_keys_is_not_layout():
    doc = {
        "kind": "Overlay",
        "metadata": {"name": "CC-1-assignment"},
        "spec": {"target": {"kind": "MAS", "name": "sre-triage"}, "patch": {"agents": {}}},
        "x-canvas-positions": {"sre": {"x": 1, "y": 2}},
    }
    assert overlay_has_canvas_extensions(doc)
    assert overlay_targets_mas(doc, mas_name="sre-triage")
    assert not overlay_is_layout_only(doc)


def test_layout_only_is_empty_patch_plus_canvas_keys():
    assert overlay_is_layout_only(
        {
            "kind": "Overlay",
            "spec": {"target": {"kind": "MAS", "name": "demo"}, "patch": {}},
            "x-canvas-positions": {"n1": {"x": 1, "y": 2}},
        }
    )
    assert not overlay_is_layout_only(
        {"kind": "Overlay", "spec": {"target": {"kind": "MAS"}, "patch": {}}, "metadata": {"name": "demo"}}
    )


def test_apply_ignores_spec_overlay_canvas_keys(tmp_path: Path):
    overlays = tmp_path / "overlays"
    overlays.mkdir()
    (overlays / "CC-1-assignment.yaml").write_text(
        "\n".join(
            [
                "kind: Overlay",
                "metadata: {name: CC-1-assignment}",
                "spec:",
                "  target: {kind: MAS, name: demo}",
                "  patch: {params: {injected: true}}",
                "x-canvas-positions:",
                "  sre: {x: 1, y: 2}",
                "x-canvas-node-ids:",
                "  sre: {model: from_challenge}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (overlays / "demo.yaml").write_text(
        "\n".join(
            [
                "kind: Overlay",
                "metadata: {name: demo}",
                "spec:",
                "  target: {kind: MAS, name: demo}",
                "  patch: {}",
                "x-canvas-positions:",
                "  sre: {x: 9, y: 8}",
                "x-canvas-node-ids:",
                "  sre: {model: from_layout}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    mas_yaml = "kind: MAS\nmetadata:\n  name: demo\nspec: {}\n"
    agents = {"sre": "kind: Agent\nmetadata:\n  name: sre\nspec: {}\n"}
    out_mas, out_agents, path = apply_ui_canvas_to_yaml(tmp_path, mas_yaml, agents)
    assert path is not None
    assert path.name == "demo.yaml"
    assert "x: 9" in out_mas
    assert "from_layout" in out_agents["sre"]
    assert "from_challenge" not in out_agents["sre"]
    assert "injected" not in out_mas


def test_apply_projects_library_overlay_named_for_mas(tmp_path: Path):
    mas_dir = tmp_path / "apps" / "demo"
    mas_dir.mkdir(parents=True)
    lib_overlays = tmp_path / "overlays"
    lib_overlays.mkdir()
    (lib_overlays / "layout.yaml").write_text(
        "kind: Overlay\nmetadata: {name: demo}\nspec:\n"
        "  target: {kind: MAS, name: demo}\n  patch: {}\n"
        "x-canvas-positions:\n  sre: {x: 4, y: 5}\n",
        encoding="utf-8",
    )
    (lib_overlays / "global.yaml").write_text(
        "kind: Overlay\nmetadata: {name: global}\nspec:\n"
        "  target: {kind: MAS}\n  patch: {}\n"
        "x-canvas-positions:\n  sre: {x: 1, y: 1}\n",
        encoding="utf-8",
    )
    mas_yaml = "kind: MAS\nmetadata:\n  name: demo\n"
    agents = {"sre": "kind: Agent\nmetadata:\n  name: sre\n"}
    out_mas, _agents, path = apply_ui_canvas_to_yaml(
        mas_dir, mas_yaml, agents, extra_overlay_dirs=[lib_overlays]
    )
    assert path is not None
    assert path.name == "layout.yaml"
    assert "x: 4" in out_mas
    assert "x: 1" not in out_mas


def test_apply_projects_positions_and_per_agent_fields(tmp_path: Path):
    overlays = tmp_path / "overlays"
    overlays.mkdir()
    (overlays / "demo.yaml").write_text(
        "\n".join(
            [
                "apiVersion: mas/v1",
                "kind: Overlay",
                "metadata:",
                "  name: demo",
                "spec:",
                "  target: {kind: MAS, name: demo}",
                "  patch: {}",
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


def test_apply_skips_overlay_targeting_another_mas(tmp_path: Path):
    overlays = tmp_path / "overlays"
    overlays.mkdir()
    (overlays / "other.yaml").write_text(
        "kind: Overlay\nspec:\n  target: {kind: MAS, name: other}\n  patch: {}\n"
        "x-canvas-positions:\n  sre: {x: 1, y: 2}\n",
        encoding="utf-8",
    )
    mas_yaml = "kind: MAS\nmetadata:\n  name: demo\n"
    out_mas, _agents, path = apply_ui_canvas_to_yaml(tmp_path, mas_yaml, {"sre": "kind: Agent\n"})
    assert path is None
    assert "x-canvas-positions" not in out_mas


def test_project_does_not_apply_spec_patch():
    mas = {"kind": "MAS", "spec": {"agency": {"agents": []}}}
    agents = {"sre": {"kind": "Agent", "spec": {}}}
    overlay = {
        "kind": "Overlay",
        "metadata": {"name": "CC-1-assignment"},
        "spec": {
            "target": {"kind": "MAS"},
            "patch": {"params": {"injected": True}},
        },
        "x-canvas-positions": {"sre": {"x": 1, "y": 2}},
    }
    project_ui_canvas(mas, agents, overlay)
    assert mas["x-canvas-positions"]["sre"]["x"] == 1
    assert mas["spec"].get("params") is None


def test_extract_binds_with_spec_target():
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
    assert overlay["metadata"]["name"] == "demo"
    assert overlay["spec"]["target"] == {"kind": "MAS", "name": "demo"}
    assert overlay["spec"]["patch"] == {}
    assert "x-ui-canvas" not in overlay
    assert "x-canvas-positions" not in mas
    assert overlay["x-text-input"]["sre"] == "q"


def test_extract_picks_root_level_text_input():
    mas = {"kind": "MAS", "x-canvas-positions": {"n": {"x": 0, "y": 0}}}
    agents = {
        "qa-agent": {
            "kind": "Agent",
            "metadata": {"name": "qa-agent", "x-node-id": "node_qa"},
            "x-text-input": "what are the top attractions to visit in Paris?",
            "x-canvas-node-ids": {"model": "node_model"},
        }
    }
    overlay = extract_ui_canvas_overlay(mas, agents, mas_name="qa-mas")
    assert overlay is not None
    assert overlay["x-text-input"]["qa-agent"].startswith("what are the top")
    assert "x-text-input" not in agents["qa-agent"]
    assert "x-node-id" not in agents["qa-agent"]["metadata"]


def test_write_uses_mas_name_not_reserved_filename(tmp_path: Path):
    overlay = extract_ui_canvas_overlay(
        {"x-canvas-positions": {"n": {"x": 1, "y": 2}}},
        {},
        mas_name="trip-planner",
    )
    assert overlay is not None
    dest = write_ui_canvas_overlay(tmp_path, overlay, mas_name="trip-planner")
    assert dest.name == "trip-planner.yaml"
    assert find_layout_overlay(tmp_path, "trip-planner") == dest


def test_write_does_not_clobber_behavioral_overlay(tmp_path: Path):
    overlays = tmp_path / "overlays"
    overlays.mkdir()
    (overlays / "demo.yaml").write_text(
        "kind: Overlay\nmetadata: {name: demo}\nspec:\n  target: {kind: MAS, name: demo}\n"
        "  patch: {agents: {}}\nx-canvas-positions:\n  n: {x: 0, y: 0}\n",
        encoding="utf-8",
    )
    overlay = extract_ui_canvas_overlay(
        {"x-canvas-positions": {"n": {"x": 3, "y": 4}}},
        {},
        mas_name="demo",
    )
    assert overlay is not None
    dest = write_ui_canvas_overlay(tmp_path, overlay, mas_name="demo")
    assert dest.name != "demo.yaml"
    behavioral = (overlays / "demo.yaml").read_text(encoding="utf-8")
    assert "agents:" in behavioral


def test_persist_skips_unparseable_agent(tmp_path: Path):
    mas_yaml, agents = persist_ui_canvas_yaml(
        tmp_path,
        "kind: MAS\nmetadata: {name: demo}\nx-canvas-positions: {n: {x: 1, y: 2}}\n",
        {"coordinator": "spec: [\n"},
        mas_name="demo",
    )
    assert "x-canvas-positions" in mas_yaml
    assert agents["coordinator"] == "spec: [\n"
    assert not (tmp_path / "overlays").exists()


def test_overlay_document_canvas_roundtrip(tmp_path: Path):
    spec = {
        "kind": "Overlay",
        "metadata": {"name": "cot-moderator"},
        "spec": {"target": {"kind": "MAS"}, "patch": {"agents": {"moderator": {"design_pattern": {"type": "cot"}}}}},
        "x-canvas-positions": {"n1": {"x": 1, "y": 2}},
        "x-canvas-node-ids": {"moderator": {"agent": "n1"}},
    }
    layout = extract_overlay_document_canvas(spec, overlay_name="cot-moderator")
    assert layout is not None
    assert layout["spec"]["target"] == {"kind": "Overlay", "name": "cot-moderator"}
    assert layout["spec"]["patch"] == {}
    assert "x-canvas-positions" not in spec
    dest = write_ui_canvas_overlay(
        tmp_path, layout, target_kind="Overlay", target_name="cot-moderator"
    )
    assert dest.name == "cot-moderator.yaml"
    spec_yaml = (
        "kind: Overlay\nmetadata: {name: cot-moderator}\n"
        "spec:\n  target: {kind: MAS}\n  patch: {agents: {moderator: {}}}\n"
    )
    (tmp_path / "cot-moderator.yaml").write_text(spec_yaml, encoding="utf-8")
    dest = write_ui_canvas_overlay(
        tmp_path, layout, target_kind="Overlay", target_name="cot-moderator"
    )
    assert dest.name == "cot-moderator-layout.yaml"
    projected, path = apply_ui_canvas_to_overlay_yaml(tmp_path, spec_yaml)
    assert path == dest
    assert "x-canvas-positions" in projected
    assert "agents:" in projected


def test_persist_overlay_peels_canvas_off_spec(tmp_path: Path):
    yaml_text = (
        "kind: Overlay\nmetadata: {name: react-moderator}\n"
        "spec:\n  target: {kind: MAS}\n  patch: {agents: {moderator: {}}}\n"
        "x-canvas-positions:\n  n: {x: 3, y: 4}\n"
    )
    out = persist_overlay_canvas_yaml(tmp_path, yaml_text, overlay_name="react-moderator")
    assert "x-canvas-positions" not in out
    assert "agents:" in out
    layout = tmp_path / "react-moderator.yaml"
    assert layout.is_file()
    text = layout.read_text(encoding="utf-8")
    assert "x-canvas-positions" in text
    assert "kind: Overlay" in text


def test_merge_overlay_empty_patch_copies_root_x_fields():
    base = {"apiVersion": "mas/v1", "kind": "MAS", "metadata": {"name": "sre-triage"}, "spec": {}}
    overlay = {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "sre-triage"},
        "spec": {"target": {"kind": "MAS", "name": "sre-triage"}, "patch": {}},
        "x-canvas-positions": {"sre": {"x": 0, "y": -350}},
        "x-canvas-node-ids": {"sre": {"role": "node_role"}},
    }
    merged = merge_overlay(base, overlay)
    assert merged["x-canvas-positions"] == {"sre": {"x": 0, "y": -350}}
    assert merged["x-canvas-node-ids"]["sre"]["role"] == "node_role"
    assert "x-canvas-positions" not in merged.get("spec", {})


def test_apply_registered_overlays_skip_folder_scan(tmp_path: Path):
    mas_dir = tmp_path / "apps" / "demo"
    (mas_dir / "overlays").mkdir(parents=True)
    (mas_dir / "overlays" / "on-disk.yaml").write_text(
        "kind: Overlay\nmetadata: {name: demo}\nspec:\n"
        "  target: {kind: MAS, name: demo}\n  patch: {}\n"
        "x-canvas-positions:\n  sre: {x: 1, y: 1}\n",
        encoding="utf-8",
    )
    registered_path = tmp_path / "from-registry.yaml"
    registered_doc = {
        "kind": "Overlay",
        "metadata": {"name": "demo"},
        "spec": {"target": {"kind": "MAS", "name": "demo"}, "patch": {}},
        "x-canvas-positions": {"sre": {"x": 9, "y": 8}},
    }
    mas_yaml = "kind: MAS\nmetadata:\n  name: demo\n"
    agents = {"sre": "kind: Agent\nmetadata:\n  name: sre\n"}
    empty_mas, _agents, empty_path = apply_ui_canvas_to_yaml(
        mas_dir, mas_yaml, agents, overlays=[]
    )
    assert empty_path is None
    assert "x-canvas-positions" not in empty_mas
    out_mas, _out_agents, path = apply_ui_canvas_to_yaml(
        mas_dir, mas_yaml, agents, overlays=[(registered_path, registered_doc)]
    )
    assert path == registered_path
    assert "x: 9" in out_mas
    assert "x: 1" not in out_mas
