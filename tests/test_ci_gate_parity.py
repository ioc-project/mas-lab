#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A gate that only main runs turns main red after a green pull request: keep the two the same."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def _package_coverage_steps() -> list[tuple[dict, dict]]:
    doc = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8"))
    return [
        (job, step)
        for job in doc["jobs"].values()
        for step in job.get("steps", [])
        if isinstance(step, dict) and "COVERAGE_PACKAGE" in str(step.get("run", ""))
    ]


def test_package_coverage_runs_on_pull_requests_like_on_main():
    found = _package_coverage_steps()
    assert found, "the unit job no longer passes COVERAGE_PACKAGE"
    for job, step in found:
        for where, text in (("run", step["run"]), ("step if", step.get("if", "")), ("job if", job.get("if", ""))):
            assert "github.event_name" not in str(text), f"package coverage depends on the event in the {where}"
        assert re.search(r"COVERAGE_PACKAGE=1\b", step["run"]), step["run"]


def test_the_new_parity_test_runs_in_ci():
    text = (REPO_ROOT / "Taskfile.yml").read_text(encoding="utf-8")
    assert text.count("tests/test_ci_gate_parity.py") >= 2  # verify-unit and verify-repo


def test_package_coverage_floors_are_set_and_not_above_the_target():
    taskfile = yaml.safe_load((REPO_ROOT / "Taskfile.yml").read_text(encoding="utf-8"))
    for name in ("PACKAGE_FLOOR_CTL", "PACKAGE_FLOOR_LAB"):
        assert 0 < int(taskfile["vars"][name]) <= 90, name


def test_every_package_coverage_gate_names_its_floor():
    text = (REPO_ROOT / "Taskfile.yml").read_text(encoding="utf-8")
    thresholds = re.findall(r"coverage report --fail-under=(\S+)", text)
    assert thresholds
    for value in thresholds:
        assert value == "90" or value.startswith("{{.PACKAGE_FLOOR_"), value
