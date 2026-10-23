#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/ci_paths.py"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "ci_paths.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("ci_paths", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def paths():
    return _load_module()


def test_docs_only_skips_unit_and_ioa(paths):
    flags = paths.classify(["docs/getting-started/developer.md"], event="pull_request")
    assert flags["lint"] is True
    assert flags["docs"] is True
    assert flags["unit"] is False
    assert flags["ioa"] is False
    assert flags["smoke"] is False
    assert flags["lib_standard"] is False


def test_library_standard_is_independent(paths):
    flags = paths.classify(["library-standard/src/mas/library/standard/__init__.py"], event="pull_request")
    assert flags["lib_standard"] is True
    assert flags["unit"] is False
    assert flags["lib_eval"] is False
    assert flags["ioa"] is False
    assert flags["smoke"] is False


def test_library_ioa_runs_library_and_compliance(paths):
    flags = paths.classify(["library-ioa/src/library_ioa/__init__.py"], event="pull_request")
    assert flags["lib_ioa"] is True
    assert flags["ioa"] is True
    assert flags["unit"] is False
    assert flags["unit_311"] is False


def test_runtime_does_not_run_a2a_or_golden(paths):
    flags = paths.classify(["runtime/src/mas/runtime/kernel/engine.py"], event="pull_request")
    assert flags["unit"] is True
    assert flags["hitl"] is True
    assert flags["smoke"] is True
    assert flags["golden"] is False
    assert flags["controller"] is False
    assert flags["ioa"] is False
    assert flags["lib_standard"] is False
    assert flags["demo_ui"] is False


def test_demo_ui_only(paths):
    flags = paths.classify(["demo-ui/src/app.js"], event="pull_request")
    assert flags["demo_ui"] is True
    assert flags["unit"] is False
    assert flags["docs"] is False


def test_tutorial_docs_run_tutorial_tests_not_unit(paths):
    flags = paths.classify(["docs/tutorials/01-building-an-agent/README.md"], event="pull_request")
    assert flags["tutorials"] is True
    assert flags["docs"] is True
    assert flags["unit"] is False


def test_hitl_test_does_not_run_golden(paths):
    flags = paths.classify(["tests/test_hitl_tool_gate.py"], event="pull_request")
    assert flags["hitl"] is True
    assert flags["golden"] is False
    assert flags["unit"] is False


def test_unit_test_file_does_not_run_smoke(paths):
    flags = paths.classify(["tests/test_ci_paths.py"], event="pull_request")
    assert flags["unit"] is True
    assert flags["smoke"] is False
    assert flags["lib_kg"] is False


def test_push_to_main_enables_unit_311(paths):
    flags = paths.classify(["ctl/src/mas/ctl/cli.py"], event="push")
    assert flags["unit"] is True
    assert flags["unit_311"] is True


def test_force_all_enables_every_job(paths):
    flags = paths.classify([], event="workflow_dispatch", force_all=True)
    assert all(flags.values())


def test_github_workflow_change_runs_the_graph(paths):
    flags = paths.classify([".github/workflows/test.yml"], event="pull_request")
    assert flags["unit"] is True
    assert flags["smoke"] is True
    assert flags["lib_standard"] is True
    assert flags["ioa"] is True
    assert flags["unit_311"] is False
