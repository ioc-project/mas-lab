#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``scripts/version_manager.py``: every package name the workflows pass must be one it knows."""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "version_manager.py"
_spec = importlib.util.spec_from_file_location("version_manager", SCRIPT)
assert _spec and _spec.loader
manager = importlib.util.module_from_spec(_spec)
sys.modules["version_manager"] = manager
_spec.loader.exec_module(manager)

# Short names used by .github/workflows/*.yml and Taskfile.yml → the wheel name that keys PACKAGES.
SHORT_NAMES = {
    "runtime": "mas-lab-runtime",
    "library": "mas-lab-library-standard",
    "ctl": "mas-lab-ctl",
    "core": "mas-lab-component-core",
    "bench": "mas-lab-component-bench",
    "controller": "mas-lab-component-controller",
    "telemetry": "mas-lab-library-telemetry",
    "kg": "mas-lab-library-kg",
    "lab": "mas-lab",
}


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, cwd=REPO_ROOT)


def test_the_default_package_is_one_it_knows() -> None:
    assert "mas-lab" in manager.PACKAGES
    assert manager.read_pyproject_version("mas-lab") == manager.read_root_version()


def test_the_rolling_workflow_names_only_known_packages() -> None:
    text = (REPO_ROOT / ".github/workflows/rolling-packages.yml").read_text(encoding="utf-8")
    packages = re.findall(r"^\s+package: (\S+)$", text, re.MULTILINE)
    assert packages and set(packages) <= set(manager.PACKAGES)
    assert len(packages) == len(re.findall(r"^          - name: ", text, re.MULTILINE))  # one per matrix entry


def test_the_publish_workflow_asks_for_a_known_package() -> None:
    text = (REPO_ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
    asked = re.findall(r"version_manager\.py get (?!\$\{\{)(\S+?)\)", text)
    assert asked and set(asked) <= set(manager.PACKAGES)


def test_get_accepts_every_short_name() -> None:
    for short, wheel in SHORT_NAMES.items():
        got = _run("get", short)
        want = _run("get", wheel)
        assert got.returncode == 0, got.stderr
        assert want.returncode == 0, want.stderr
        assert got.stdout == want.stdout


def test_get_without_package_reads_the_lab_version() -> None:
    got = _run("get")
    assert got.returncode == 0, got.stderr
    assert got.stdout == _run("get", "mas-lab").stdout


def test_every_alias_points_at_a_real_package() -> None:
    for short, wheel in manager.ALIASES.items():
        assert wheel in manager.PACKAGES
