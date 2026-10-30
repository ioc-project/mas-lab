#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Packaging-hygiene guards.

These tests lock in fixes that are otherwise easy to silently regress:

* No local run output may live inside packaged source (it would ship to PyPI).
* The umbrella ``mas-lab`` wheel must not re-ship modules owned by its
  components (``benchmark/`` -> mas-lab-bench, ``telemetry/`` -> mas-lab-core),
  which would create install-order-dependent file collisions.
* All ``mas`` / ``mas.lab`` namespace contributions must use PEP 420 implicit
  namespaces consistently (no stray ``__init__.py`` namespace markers), so
  components do not shadow each other.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Every package src tree that installs into the shared ``mas`` namespace.
NAMESPACE_PACKAGE_SRCS = [
    "lab/src",
    "lab/components/core/src",
    "lab/components/bench/src",
    "lab/components/controller/src",
]


def test_no_run_output_inside_packaged_source():
    output_dir = REPO_ROOT / "lab/src/mas/lab/output"
    assert not output_dir.exists(), (
        "Run output found inside packaged source; it would ship in the wheel. "
        "Remove lab/src/mas/lab/output and keep it gitignored."
    )


def test_umbrella_does_not_reship_component_modules():
    umbrella = REPO_ROOT / "lab/src/mas/lab"
    for owned in ("benchmark", "telemetry"):
        assert not (umbrella / owned).exists(), (
            f"lab/src/mas/lab/{owned} is owned by a component package; the umbrella "
            "must not also ship it (file collision on install)."
        )


_PRIVATE_INFRA_SHA256 = {
    "95b29ebf7d02e5cc6843ddf8dfc55faebcd51e395cc4390ee770de9ffad1d959",
    "7caed82e7fdac9b6416007fc00378d02fe8eef40365f4e61b8daab7f1f3c681e",
}
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{7,}")
_SCAN_ROOTS = (
    "ctl",
    "runtime",
    "lab",
    "library-standard",
    "library-skills",
    "library-samples",
    "library-lab",
    "library-eval",
    "library-ioa",
    "library-telemetry",
    "library-kg",
    "docs",
    "tests",
)
_SCAN_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".toml", ".json", ".txt"}
_SKIP_DIR_NAMES = {".git", ".venv", "__pycache__", "node_modules", "dist", "build", ".mypy_cache", ".ruff_cache"}


def test_no_private_infra_names_in_oss_tree():
    """User-local infra names must not ship in this repository."""
    hits: list[str] = []
    for root_name in _SCAN_ROOTS:
        root = REPO_ROOT / root_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if any(part in _SKIP_DIR_NAMES for part in path.parts):
                continue
            if not path.is_file() or path.suffix not in _SCAN_SUFFIXES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for token in set(_TOKEN_RE.findall(text)):
                digest = hashlib.sha256(token.encode()).hexdigest()
                if digest in _PRIVATE_INFRA_SHA256:
                    hits.append(str(path.relative_to(REPO_ROOT)))
    assert hits == [], "private infra names leaked into OSS sources:\n" + "\n".join(hits)


def test_namespace_packages_use_pep420_consistently():
    """No package may ship mas/__init__.py or mas/lab/__init__.py."""
    offenders = []
    for src in NAMESPACE_PACKAGE_SRCS:
        for marker in ("mas/__init__.py", "mas/lab/__init__.py"):
            if (REPO_ROOT / src / marker).exists():
                offenders.append(f"{src}/{marker}")
    assert not offenders, (
        "These namespace markers break PEP 420 consistency and can shadow "
        f"sibling packages: {offenders}"
    )
