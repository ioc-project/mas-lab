#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``scripts/version_manager.py``: every package name the workflows pass must be one it knows."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("version_manager", REPO_ROOT / "scripts" / "version_manager.py")
assert _spec and _spec.loader
manager = importlib.util.module_from_spec(_spec)
sys.modules["version_manager"] = manager
_spec.loader.exec_module(manager)


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
