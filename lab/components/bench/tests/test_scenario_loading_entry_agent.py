#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""MAS overlay $entry applies design_pattern only to workflow.entry."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.ctl.session.flavour_control import ControlError
from mas.lab.lab.config.scenario_loading import _apply_control_pipeline, load_stacked_config

REPO = Path(__file__).resolve().parents[4]
MAS = REPO / "library-samples" / "apps" / "trip-planner" / "mas.yaml"


def _write(directory: Path, name: str, body: str) -> None:
    (directory / name).write_text(body, encoding="utf-8")


def test_entry_overlay_patches_workflow_entry_only(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pattern-cot.yaml",
        """
apiVersion: mas/v1
kind: Overlay
metadata:
  id: pattern-cot
spec:
  target:
    kind: MAS
  patch:
    agents:
      "$entry":
        design_pattern:
          type: cot
          config:
            max_steps: 10
""",
    )
    cfg, _ = load_stacked_config(MAS, ["pattern-cot"], overlays_dir=tmp_path, base_dir=tmp_path)
    by_id = {a["id"]: a for a in cfg["agents"]}
    assert cfg["workflow"]["entry"] == "moderator"
    assert by_id["moderator"]["design_pattern"]["type"] == "cot"
    assert "design_pattern" not in by_id["schedule_agent"]
    assert "design_pattern" not in by_id["itinerary_agent"]
    assert "design_pattern" not in by_id["concierge_agent"]


def test_entry_overlay_follows_workflow_from_an_earlier_overlay(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "shift-entry.yaml",
        """
apiVersion: mas/v1
kind: Overlay
metadata:
  id: shift-entry
spec:
  target:
    kind: MAS
  patch:
    workflow:
      entry: schedule_agent
      nodes:
        - id: schedule_agent
        - id: moderator
""",
    )
    _write(
        tmp_path,
        "pattern-react.yaml",
        """
apiVersion: mas/v1
kind: Overlay
metadata:
  id: pattern-react
spec:
  target:
    kind: MAS
  patch:
    agents:
      "$entry":
        design_pattern:
          type: react
""",
    )
    cfg, _ = load_stacked_config(
        MAS,
        ["shift-entry", "pattern-react"],
        overlays_dir=tmp_path,
        base_dir=tmp_path,
    )
    by_id = {a["id"]: a for a in cfg["agents"]}
    assert by_id["schedule_agent"]["design_pattern"]["type"] == "react"
    assert "design_pattern" not in by_id["moderator"]


def test_stacked_control_entry_selector_uses_workflow_entry(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "shift-entry.yaml",
        """
apiVersion: mas/v1
kind: Overlay
metadata:
  id: shift-entry
spec:
  target:
    kind: MAS
  patch:
    workflow:
      entry: schedule_agent
      nodes:
        - id: schedule_agent
        - id: moderator
""",
    )
    _write(
        tmp_path,
        "entry-gov.yaml",
        """
apiVersion: control/v1
kind: Control
metadata: {name: entry-gov}
spec:
  agents: $entry
  governance: [sample_governance]
""",
    )
    cfg, _ = load_stacked_config(
        MAS,
        ["shift-entry", "entry-gov"],
        overlays_dir=tmp_path,
        base_dir=tmp_path,
    )
    by_id = {a["id"]: a for a in cfg["agents"]}
    assert cfg["workflow"]["entry"] == "schedule_agent"
    assert "sample_governance" in (by_id["schedule_agent"].get("governance") or [])
    assert "sample_governance" not in (by_id["moderator"].get("governance") or [])


def test_stacked_control_delegates_selector_uses_delegates_to(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "delegates-gov.yaml",
        """
apiVersion: control/v1
kind: Control
metadata: {name: delegates-gov}
spec:
  agents: $delegates
  governance: [sample_governance]
""",
    )
    cfg, _ = load_stacked_config(MAS, ["delegates-gov"], overlays_dir=tmp_path, base_dir=tmp_path)
    by_id = {a["id"]: a for a in cfg["agents"]}
    assert "sample_governance" not in (by_id["moderator"].get("governance") or [])
    for agent_id in ("schedule_agent", "itinerary_agent", "concierge_agent"):
        assert "sample_governance" in (by_id[agent_id].get("governance") or []), agent_id


def test_stacked_control_empty_agents_list_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "nobody.yaml"
    overlay = {
        "apiVersion": "control/v1",
        "kind": "Control",
        "metadata": {"name": "nobody"},
        "spec": {"agents": [], "governance": ["sample_governance"]},
    }
    with pytest.raises(ControlError, match="invalid"):
        _apply_control_pipeline(overlay, {"agents": [{"id": "desk"}]}, overlay_path=path)


def test_stacked_control_entry_without_workflow_matches_nobody(tmp_path: Path) -> None:
    path = tmp_path / "entry-gov.yaml"
    overlay = {
        "apiVersion": "control/v1",
        "kind": "Control",
        "metadata": {"name": "entry-gov"},
        "spec": {"agents": "$entry", "governance": ["sample_governance"]},
    }
    config = {"agents": [{"id": "desk"}, {"id": "bill"}]}
    _apply_control_pipeline(overlay, config, overlay_path=path)
    assert "sample_governance" not in (config["agents"][0].get("governance") or [])
    assert "sample_governance" not in (config["agents"][1].get("governance") or [])


def test_stacked_control_delegates_without_a_list_match_nobody(tmp_path: Path) -> None:
    path = tmp_path / "delegates-gov.yaml"
    overlay = {
        "apiVersion": "control/v1",
        "kind": "Control",
        "metadata": {"name": "delegates-gov"},
        "spec": {"agents": "$delegates", "governance": ["sample_governance"]},
    }
    config = {
        "agents": [{"id": "desk"}, {"id": "bill"}],
        "workflow": {"entry": "desk", "nodes": [{"id": "desk"}]},
    }
    _apply_control_pipeline(overlay, config, overlay_path=path)
    assert "sample_governance" not in (config["agents"][0].get("governance") or [])
    assert "sample_governance" not in (config["agents"][1].get("governance") or [])


def test_stacked_control_resolves_paths_against_the_control_file(tmp_path: Path) -> None:
    path = tmp_path / "debug.yaml"
    path.write_text(
        "apiVersion: control/v1\nkind: Control\nmetadata: {name: debug}\n"
        "spec:\n  governance:\n    - gdb: {script_file: ./break.gdb}\n",
        encoding="utf-8",
    )
    overlay = {
        "apiVersion": "control/v1",
        "kind": "Control",
        "metadata": {"name": "debug"},
        "spec": {"governance": [{"gdb": {"script_file": "./break.gdb"}}]},
    }
    config = {"agents": [{"id": "desk"}], "workflow": {"entry": "desk"}}
    _apply_control_pipeline(overlay, config, overlay_path=path)
    item = config["agents"][0]["governance"][0]["gdb"]
    assert Path(item["script_file"]).resolve() == (tmp_path / "break.gdb").resolve()
    assert "manifest_dir" not in item


def test_stacked_control_refuses_an_absolute_path(tmp_path: Path) -> None:
    path = tmp_path / "abs.yaml"
    overlay = {
        "apiVersion": "control/v1",
        "kind": "Control",
        "metadata": {"name": "abs"},
        "spec": {"governance": [{"gdb": {"script_file": "/etc/break.gdb"}}]},
    }
    with pytest.raises(ControlError, match="absolute path"):
        _apply_control_pipeline(overlay, {"agents": [{"id": "desk"}]}, overlay_path=path)


def test_agent_collection_overlay_replaces_agents_with_op_syntax(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "single-agent.yaml",
        """
apiVersion: mas/v1
kind: Overlay
metadata:
  id: single-agent
spec:
  target:
    kind: MAS
  patch:
    agents:
      $op:
        remove: [moderator, schedule_agent, itinerary_agent, concierge_agent]
        add:
          - id: generalist
            ref: ../../../library-samples/apps/trip-planner/agents/generalist/agent.yaml
    workflow:
      entry: generalist
      nodes:
        - id: generalist
""",
    )

    cfg, _ = load_stacked_config(MAS, ["single-agent"], overlays_dir=tmp_path, base_dir=tmp_path)

    assert [agent["id"] for agent in cfg["agents"]] == ["generalist"]
    assert cfg["workflow"]["entry"] == "generalist"


def test_agent_collection_overlay_removes_entries_after_replace(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "replace-remove.yaml",
        """
apiVersion: mas/v1
kind: Overlay
metadata:
  id: replace-remove
spec:
  target:
    kind: MAS
  patch:
    agents:
      $op:
        replace:
          - id: moderator
            ref: agents/moderator.yaml
        remove: [moderator]
""",
    )

    cfg, _ = load_stacked_config(MAS, ["replace-remove"], overlays_dir=tmp_path, base_dir=tmp_path)

    assert cfg["agents"] == []


def test_agent_collection_overlay_can_readd_removed_agent(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "remove-add.yaml",
        """
apiVersion: mas/v1
kind: Overlay
metadata:
  id: remove-add
spec:
  target:
    kind: MAS
  patch:
    agents:
      $op:
        remove: [moderator]
        add:
          - id: moderator
            ref: agents/moderator.yaml
""",
    )

    cfg, _ = load_stacked_config(MAS, ["remove-add"], overlays_dir=tmp_path, base_dir=tmp_path)

    assert [agent["id"] for agent in cfg["agents"]] == [
        "schedule_agent",
        "itinerary_agent",
        "concierge_agent",
        "moderator",
    ]


def test_stacked_control_replaces_leftover_when_it_names_the_list(tmp_path: Path) -> None:
    overlay = {
        "apiVersion": "control/v1",
        "kind": "Control",
        "metadata": {"name": "gov"},
        "spec": {"governance": ["sample_governance"]},
    }
    config = {"agents": [{"id": "desk", "governance": ["retry_on_error"]}]}
    _apply_control_pipeline(overlay, config, overlay_path=tmp_path / "gov.yaml")
    assert config["agents"][0]["governance"] == ["sample_governance"]


def test_stacked_control_empty_list_unreads_leftover(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from mas.ctl.session.notices import reset_notices

    reset_notices()
    overlay = {
        "apiVersion": "control/v1",
        "kind": "Control",
        "metadata": {"name": "gov"},
        "spec": {"governance": []},
    }
    config = {"agents": [{"id": "desk", "governance": ["retry_on_error"]}]}
    _apply_control_pipeline(overlay, config, overlay_path=tmp_path / "gov.yaml")
    assert config["agents"][0]["governance"] == []
    assert "empty governance list" in capsys.readouterr().err


def test_stacked_control_does_not_copy_onto_a_non_matching_agent(tmp_path: Path) -> None:
    overlay = {
        "apiVersion": "control/v1",
        "kind": "Control",
        "metadata": {"name": "gov"},
        "spec": {"agents": "desk", "governance": ["sample_governance"]},
    }
    config = {
        "agents": [
            {"id": "desk"},
            {"id": "bill", "governance": ["retry_on_error"]},
        ]
    }
    _apply_control_pipeline(overlay, config, overlay_path=tmp_path / "gov.yaml")
    assert config["agents"][0]["governance"] == ["sample_governance"]
    assert config["agents"][1]["governance"] == ["retry_on_error"]
    assert "governance" in config["agents"][0]
    assert "sample_governance" not in (config["agents"][1].get("governance") or [])
