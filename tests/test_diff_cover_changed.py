#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/diff_cover_changed.py"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "diff_cover_changed.py"

COBERTURA = """\
<?xml version="1.0" ?>
<coverage>
  <packages>
    <package>
      <classes>
        <class filename="mas/runtime/kernel/engine.py">
          <lines>
            <line number="10" hits="1"/>
            <line number="11" hits="1"/>
            <line number="12" hits="0"/>
            <line number="20" hits="1"/>
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
"""


def _load_module():
    spec = importlib.util.spec_from_file_location("diff_cover_changed", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def gate():
    return _load_module()


def test_parse_cobertura_line_hits(gate):
    coverage = gate.parse_cobertura(COBERTURA)
    assert coverage["mas/runtime/kernel/engine.py"][10] == 1
    assert coverage["mas/runtime/kernel/engine.py"][12] == 0


def test_parse_unified_diff_added_lines(gate):
    diff = """\
diff --git a/runtime/src/mas/runtime/kernel/engine.py b/runtime/src/mas/runtime/kernel/engine.py
--- a/runtime/src/mas/runtime/kernel/engine.py
+++ b/runtime/src/mas/runtime/kernel/engine.py
@@ -9,0 +10,3 @@
+covered
+also
+missed
"""
    changed = gate.parse_unified_diff(diff)
    assert changed["runtime/src/mas/runtime/kernel/engine.py"] == {10, 11, 12}


def test_evaluate_passes_when_changed_lines_are_covered(gate):
    coverage = gate.parse_cobertura(COBERTURA)
    changed = {"runtime/src/mas/runtime/kernel/engine.py": {10, 11, 20}}
    code, report = gate.evaluate(coverage, changed, fail_under=90)
    assert code == 0
    assert "PASS" in report


def test_evaluate_fails_when_changed_line_is_uncovered(gate):
    coverage = gate.parse_cobertura(COBERTURA)
    changed = {"runtime/src/mas/runtime/kernel/engine.py": {10, 12}}
    code, report = gate.evaluate(coverage, changed, fail_under=90)
    assert code == 1
    assert "engine.py:12" in report
    assert "FAIL" in report


def test_evaluate_skips_tests_and_scripts(gate):
    coverage = gate.parse_cobertura(COBERTURA)
    changed = {
        "tests/test_diff_cover_changed.py": {4, 5},
        "scripts/diff_cover_changed.py": {20},
    }
    code, report = gate.evaluate(coverage, changed, fail_under=90)
    assert code == 0
    assert "skipping gate" in report


def test_evaluate_treats_new_kernel_file_without_xml_as_uncovered(gate):
    coverage = gate.parse_cobertura(COBERTURA)
    changed = {"runtime/src/mas/runtime/kernel/brand_new.py": {1, 2, 3}}
    code, report = gate.evaluate(coverage, changed, fail_under=90)
    assert code == 1
    assert "brand_new.py:1" in report


def test_evaluate_prefix_limits_measured_trees(gate):
    coverage = gate.parse_cobertura(COBERTURA)
    changed = {
        "runtime/src/mas/runtime/kernel/engine.py": {12},
        "library-standard/src/mas/library/standard/x.py": {1},
    }
    code, report = gate.evaluate(
        coverage,
        changed,
        fail_under=90,
        prefixes=("library-standard/src/",),
    )
    assert code == 1
    assert "library-standard" in report
    assert "engine.py" not in report


def test_evaluate_ignores_non_executable_lines_when_file_is_in_report(gate):
    coverage = gate.parse_cobertura(COBERTURA)
    changed = {"runtime/src/mas/runtime/kernel/engine.py": {10, 13}}
    code, report = gate.evaluate(coverage, changed, fail_under=90)
    assert code == 0
    assert "PASS" in report
