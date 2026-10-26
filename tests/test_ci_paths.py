#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/ci_paths.py"""

from __future__ import annotations

import importlib.util
import json
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
    assert flags["runtime"] is False
    assert flags["mcp"] is False
    assert flags["a2a"] is False
    assert flags["smoke"] is False
    assert flags["lib_standard"] is False


def test_library_standard_is_independent(paths):
    flags = paths.classify(["library-standard/src/mas/library/standard/__init__.py"], event="pull_request")
    assert flags["lib_standard"] is True
    assert flags["runtime"] is False
    assert flags["lib_eval"] is False
    assert flags["mcp"] is False
    assert flags["a2a"] is False
    assert flags["smoke"] is False


def test_library_ioa_shared_skips_compliance(paths):
    flags = paths.classify(["library-ioa/src/library_ioa/__init__.py"], event="pull_request")
    assert flags["lib_ioa"] is True
    assert flags["mcp"] is False
    assert flags["a2a"] is False
    assert flags["runtime"] is False
    assert flags["unit_311"] is False


def test_mcp_plugin_runs_mcp_not_a2a(paths):
    flags = paths.classify(["library-ioa/src/library_ioa/plugins/mcp/provider.py"])
    assert flags["lib_ioa"] is True
    assert flags["mcp"] is True
    assert flags["a2a"] is False


def test_a2a_plugin_runs_a2a_not_mcp(paths):
    flags = paths.classify(["library-ioa/src/library_ioa/plugins/a2a/agentcard.py"])
    assert flags["lib_ioa"] is True
    assert flags["a2a"] is True
    assert flags["mcp"] is False


def test_mcp_compliance_tree_runs_mcp(paths):
    flags = paths.classify(["library-ioa/compliance/mcp/mcp-conformance.sh"])
    assert flags["mcp"] is True
    assert flags["a2a"] is False
    assert flags["lib_ioa"] is True


def test_runtime_does_not_run_a2a_or_golden(paths):
    flags = paths.classify(["runtime/src/mas/runtime/kernel/engine.py"], event="pull_request")
    assert flags["runtime"] is True
    assert flags["ctl"] is False
    assert flags["lab_unit"] is False
    assert flags["repo"] is False
    assert flags["hitl"] is True
    assert flags["smoke"] is True
    assert flags["golden"] is False
    assert flags["controller"] is False
    assert flags["mcp"] is False
    assert flags["a2a"] is False
    assert flags["lib_standard"] is False
    assert flags["demo_ui"] is False


def test_demo_ui_only(paths):
    flags = paths.classify(["demo-ui/src/app.js"], event="pull_request")
    assert flags["demo_ui"] is True
    assert flags["runtime"] is False
    assert flags["docs"] is False


def test_tutorial_docs_run_tutorial_tests_not_unit(paths):
    flags = paths.classify(["docs/tutorials/01-building-an-agent/README.md"], event="pull_request")
    assert flags["tutorials"] is True
    assert flags["docs"] is True
    assert flags["runtime"] is False


def test_hitl_test_does_not_run_golden(paths):
    flags = paths.classify(["tests/test_hitl_tool_gate.py"], event="pull_request")
    assert flags["hitl"] is True
    assert flags["golden"] is False
    assert flags["runtime"] is False
    assert flags["repo"] is False


def test_ci_script_test_runs_repo_not_runtime(paths):
    flags = paths.classify(["tests/test_ci_paths.py"], event="pull_request")
    assert flags["repo"] is True
    assert flags["runtime"] is False
    assert flags["ctl"] is False
    assert flags["smoke"] is False
    assert flags["lib_kg"] is False


def test_ctl_does_not_run_runtime(paths):
    flags = paths.classify(["ctl/src/mas/ctl/cli.py"], event="pull_request")
    assert flags["ctl"] is True
    assert flags["runtime"] is False
    assert flags["lab_unit"] is False
    assert flags["hitl"] is True
    assert flags["unit_311"] is False


def test_push_to_main_enables_unit_311(paths):
    flags = paths.classify(["ctl/src/mas/ctl/cli.py"], event="push")
    assert flags["ctl"] is True
    assert flags["runtime"] is False
    assert flags["unit_311"] is True


def test_force_all_enables_every_job(paths):
    flags = paths.classify([], event="workflow_dispatch", force_all=True)
    assert all(flags.values())


def test_github_workflow_change_runs_the_graph(paths):
    flags = paths.classify([".github/workflows/test.yml"], event="pull_request")
    assert flags["runtime"] is True
    assert flags["ctl"] is True
    assert flags["lab_unit"] is True
    assert flags["repo"] is True
    assert flags["smoke"] is True
    assert flags["lib_standard"] is True
    assert flags["mcp"] is True
    assert flags["a2a"] is True
    assert flags["unit_311"] is False


def _parse_outputs(text: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in text.strip().splitlines())


def test_format_outputs_filters_suite_and_library_matrices(paths):
    flags = paths.classify(
        [
            "library-standard/src/mas/library/standard/__init__.py",
            "runtime/src/mas/runtime/kernel/engine.py",
        ]
    )
    parsed = _parse_outputs(paths.format_outputs(flags))
    assert parsed["lib_standard"] == "true"
    assert parsed["library"] == "true"
    assert parsed["runtime"] == "true"
    assert parsed["ctl"] == "false"
    assert parsed["unit"] == "true"
    assert parsed["suite"] == "true"
    assert parsed["hitl"] == "true"
    assert parsed["golden"] == "false"
    assert json.loads(parsed["unit_matrix"]) == {
        "include": [{"name": "Runtime", "task": "verify-runtime"}]
    }
    assert json.loads(parsed["library_matrix"]) == {
        "include": [
            {
                "name": "standard",
                "dir": "library-standard",
                "cov": "mas.library.standard",
                "fail_under": "90",
            }
        ]
    }
    suite = json.loads(parsed["suite_matrix"])
    names = {row["name"] for row in suite["include"]}
    assert names == {"HITL", "Lab smoke"}


def test_format_outputs_empty_matrices_when_docs_only(paths):
    flags = paths.classify(["docs/getting-started/developer.md"])
    parsed = _parse_outputs(paths.format_outputs(flags))
    assert parsed["suite"] == "false"
    assert parsed["library"] == "false"
    assert parsed["unit"] == "false"
    assert json.loads(parsed["suite_matrix"]) == {"include": []}
    assert json.loads(parsed["unit_matrix"]) == {"include": []}
    assert json.loads(parsed["library_matrix"]) == {"include": []}
