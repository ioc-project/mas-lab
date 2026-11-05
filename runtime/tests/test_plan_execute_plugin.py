#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for Plan-and-Execute design pattern plugin."""

from __future__ import annotations

import json

from mas.library.standard.plugins.design_patterns.plan_execute import (
    PlanExecutePlugin,
    _parse_plan,
)
from mas.runtime.kernel.state import QProduct
from mas.runtime.registry import get_registry


def test_plan_execute_registered_in_registry() -> None:
    info = get_registry().resolve("plan_execute")
    assert info is not None
    assert info.class_name == "PlanExecutePlugin"


def test_parse_plan_json_object() -> None:
    raw = json.dumps({"plan": [{"tool": "search", "arguments": {"q": "x"}}]})
    plan = _parse_plan(raw)
    assert len(plan) == 1
    assert plan[0]["tool"] == "search"


def test_parse_plan_json_list() -> None:
    raw = json.dumps([{"tool": "calc", "arguments": {"x": 1}}])
    plan = _parse_plan(raw)
    assert plan[0]["tool"] == "calc"


def test_parse_plan_fenced_json() -> None:
    raw = 'Here is the plan:\n```json\n{"plan": [{"tool": "t", "arguments": {}}]}\n```'
    plan = _parse_plan(raw)
    assert len(plan) == 1


def test_parse_plan_empty_returns_empty() -> None:
    assert _parse_plan("") == []
    assert _parse_plan("not json at all") == []


def test_protocol_lines_plan_phase() -> None:
    plugin = PlanExecutePlugin()
    q = QProduct()
    q.dp_data = {"phase": "PLAN"}
    lines = plugin.protocol_lines(q)
    assert any("PLAN-AND-EXECUTE" in line for line in lines)


def test_protocol_lines_synth_phase() -> None:
    plugin = PlanExecutePlugin()
    q = QProduct()
    q.dp_data = {"phase": "SYNTH"}
    lines = plugin.protocol_lines(q)
    assert any("SYNTHESIS" in line for line in lines)
