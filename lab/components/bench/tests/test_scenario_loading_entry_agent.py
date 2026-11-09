#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""MAS overlay $entry applies design_pattern only to workflow.entry."""

from __future__ import annotations

from pathlib import Path

from mas.lab.lab.config.scenario_loading import load_stacked_config

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
