#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

from pathlib import Path

from mas.ctl.compose.pattern_registry import pattern_for_agent, resolve_design_pattern_registry_id


def test_resolve_design_pattern_cot():
    assert resolve_design_pattern_registry_id({"type": "cot"}) == "cot@v1"
    assert resolve_design_pattern_registry_id("cot") == "cot@v1"


def test_resolve_design_pattern_plan_execute_alias():
    assert resolve_design_pattern_registry_id({"type": "plan-execute"}) == "plan_execute@v1"


def test_resolve_design_pattern_defaults_react():
    assert resolve_design_pattern_registry_id(None) == "react@v1"


def test_pattern_for_agent_reads_non_entry_agent_yaml(tmp_path: Path):
    specialist = tmp_path / "flights.yaml"
    specialist.write_text(
        "apiVersion: mas/v1\nkind: Agent\nmetadata:\n  name: flights\nspec:\n  design_pattern:\n    type: cot\n",
        encoding="utf-8",
    )
    mas = {
        "spec": {
            "agency": {
                "agents": [
                    {"id": "broker", "ref": "./broker.yaml"},
                    {"id": "flights", "ref": "./flights.yaml"},
                ]
            }
        }
    }
    assert pattern_for_agent(mas, "flights", mas_base_dir=tmp_path) == "cot@v1"
