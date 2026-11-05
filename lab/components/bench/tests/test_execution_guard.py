#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for execution plan guards."""

from __future__ import annotations

import pytest
from mas.lab.benchmark.execution.plan import build_execution_plan, enforce_max_executions


def test_build_execution_plan_coverage_order():
    plan = build_execution_plan(["s1", "s2"], [{"id": "a"}, {"id": "b"}], 2)
    assert len(plan) == 8
    assert plan[0] == ("s1", {"id": "a"}, 0)
    assert plan[1] == ("s1", {"id": "b"}, 0)
    assert plan[2] == ("s2", {"id": "a"}, 0)


def test_enforce_max_executions_ok():
    enforce_max_executions(4, {"design": {"max_executions": 10}})


def test_enforce_max_executions_raises():
    with pytest.raises(RuntimeError, match="max_executions=2"):
        enforce_max_executions(4, {"design": {"max_executions": 2}})


def test_enforce_max_executions_no_design():
    enforce_max_executions(100, {})
