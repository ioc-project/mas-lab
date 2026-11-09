#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""spec.budget had no base-schema declaration even though the runtime engine
already read it (BudgetTracker.budget_from_manifest) -- so it was rejected by
validation and unreachable from an overlay. Now real, same fix shape as
spec.params (see test_overlay_agent_patch_schema.py)."""

from __future__ import annotations

from pathlib import Path

import yaml
from mas.ctl.validate import validate_data, validate_file


def _agent(budget: dict) -> dict:
    return {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "test"},
        "spec": {"description": "test agent", "budget": budget},
    }


def test_agent_budget_field_validates() -> None:
    result = validate_data(_agent({"max_llm_calls": 50, "max_tool_calls": 100}), kind="agent")
    assert result.ok, result.issues


def test_agent_budget_empty_object_validates() -> None:
    result = validate_data(_agent({}), kind="agent")
    assert result.ok, result.issues


def test_agent_budget_unknown_field_is_rejected() -> None:
    result = validate_data(_agent({"max_tokens": 1000}), kind="agent")
    assert not result.ok


def _overlay(tmp_path: Path, patch: dict) -> Path:
    p = tmp_path / "overlay.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "kind": "Overlay",
                "apiVersion": "mas/v1",
                "metadata": {"name": "t"},
                "spec": {"target": {"kind": "Agent"}, "patch": patch},
            }
        ),
        encoding="utf-8",
    )
    return p


def test_budget_overlay_patch_validates(tmp_path: Path) -> None:
    result = validate_file(_overlay(tmp_path, {"budget": {"max_llm_calls": 50}}), kind="overlay")
    assert result.ok, result.issues


def test_budget_overlay_patch_unknown_field_is_rejected(tmp_path: Path) -> None:
    result = validate_file(_overlay(tmp_path, {"budget": {"max_cost_usd": 1.0}}), kind="overlay")
    assert not result.ok


def test_with_hardened_control_names_the_budget_plugin() -> None:
    controls_dir = (
        Path(__file__).resolve().parents[2] / "library-standard" / "src" / "mas" / "library" / "standard" / "controls"
    )
    control = yaml.safe_load((controls_dir / "with-hardened.yaml").read_text(encoding="utf-8"))
    assert "budget" not in control["spec"]
    caps = next(item["budget"] for item in control["spec"]["governance"] if isinstance(item, dict) and "budget" in item)
    assert caps == {"max_llm_calls": 50, "max_tool_calls": 100}
