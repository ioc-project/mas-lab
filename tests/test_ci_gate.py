#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/ci_gate.py"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "ci_gate.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("ci_gate", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def gate():
    return _load_module()


def test_skipped_when_not_expected_passes(gate):
    needs = {
        "changes": {"result": "success"},
        "lint": {"result": "success"},
        "unit": {"result": "success"},
        "compliance": {"result": "skipped"},
        "demo-ui": {"result": "skipped"},
        "library": {"result": "skipped"},
        "suite": {"result": "skipped"},
    }
    flags = {"unit": "true", "ioa": "false", "demo_ui": "false"}
    code, report = gate.evaluate(needs, flags)
    assert code == 0
    assert "ci-summary passed" in report


def test_skipped_when_expected_fails(gate):
    needs = {
        "changes": {"result": "success"},
        "lint": {"result": "success"},
        "unit": {"result": "skipped"},
    }
    code, report = gate.evaluate(needs, {"unit": "true"})
    assert code == 1
    assert "unit" in report


def test_library_flag_requires_library_job(gate):
    needs = {
        "changes": {"result": "success"},
        "lint": {"result": "success"},
        "library": {"result": "skipped"},
    }
    code, _report = gate.evaluate(needs, {"lib_standard": "true"})
    assert code == 1


def test_failed_unit_fails_the_gate(gate):
    needs = {
        "changes": {"result": "success"},
        "lint": {"result": "success"},
        "unit": {"result": "failure"},
    }
    code, report = gate.evaluate(needs, {"unit": "true"})
    assert code == 1
    assert "unit" in report


def test_failed_lint_fails_even_if_dependents_are_skipped(gate):
    needs = {
        "changes": {"result": "success"},
        "lint": {"result": "failure"},
        "unit": {"result": "skipped"},
    }
    code, _report = gate.evaluate(needs, {"unit": "true"})
    assert code == 1


def test_empty_needs_fails(gate):
    code, _report = gate.evaluate({})
    assert code == 1


def test_missing_expected_is_fail_closed(gate):
    needs = {
        "changes": {"result": "success"},
        "lint": {"result": "success"},
        "unit": {"result": "skipped"},
    }
    code, _report = gate.evaluate(needs, flags=None)
    assert code == 1


def test_main_reads_needs_and_expected(gate, monkeypatch, capsys):
    monkeypatch.setenv(
        "NEEDS",
        json.dumps(
            {
                "changes": {"result": "success"},
                "lint": {"result": "success"},
                "unit": {"result": "success"},
                "demo-ui": {"result": "skipped"},
            }
        ),
    )
    monkeypatch.setenv("EXPECTED", json.dumps({"unit": "true", "demo_ui": "false"}))
    assert gate.main() == 0
    assert "ci-summary passed" in capsys.readouterr().out
