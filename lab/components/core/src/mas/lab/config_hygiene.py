#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Config hygiene checker for MAS manifests.

Enforces the declarative config separation rule:
- Model names belong in agent manifests (agent.yaml), NOT in flavours,
  overlays, or mas.yaml.
- Access credentials (api_base, api_key SecretRef) belong in flavour files,
  NOT in agent.yaml, overlays, or mas.yaml.

Rules
-----
MODEL_IN_NON_AGENT
    A ``model:`` key found in a flavour, overlay, infra bundle, or mas.yaml.
    These files describe runtime wiring; model selection is an agent concern.
    Exception: ``null`` and ``mock`` values are allowed (test/no-op contexts).

ACCESS_IN_NON_FLAVOUR
    An ``api_base:``, ``api_key:``, or ``provider:`` key found in
    agent.yaml, overlays, or mas.yaml.  Access credentials live in flavour
    files so that agents are portable across deployments.

Usage
-----
Programmatic::

    from mas.lab.config_hygiene import run_hygiene_check
    violations = run_hygiene_check("/path/to/workspace")
    # violations: list[Violation]

CLI::

    mas-lab check-config [WORKSPACE_DIR]
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

_MODEL_LINE = re.compile(r"^\s+model:\s*\S")  # "    model: vertex_ai/..."
_ACCESS_LINE = re.compile(r"^\s+(api_base|api_key|provider):\s*\S")

_SKIP_DIRS = frozenset(
    {
        ".venv",
        "__pycache__",
        ".git",
        "logs",
        "output",
        "results",
        ".cache",
        "node_modules",
        ".mypy_cache",
        ".ruff_cache",
    }
)

_SEARCH_ROOTS = [
    "library-samples",
    "docs/tutorials",
    "labs",
]


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class Violation:
    path: str  # workspace-relative path
    file_kind: str  # agent | overlay | flavour | infra | mas | config | other
    line_no: int
    rule: str  # MODEL_IN_NON_AGENT | ACCESS_IN_NON_FLAVOUR
    text: str  # original line (stripped)

    def __str__(self) -> str:
        return f"  line {self.line_no} [{self.rule}]: {self.text}"


@dataclass
class HygieneReport:
    workspace: str
    violations: List[Violation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def summary(self) -> str:
        if self.ok:
            return "✓ No config hygiene violations."
        n = len(self.violations)
        files = len({v.path for v in self.violations})
        return f"✗ {n} violation{'s' if n != 1 else ''} in {files} file{'s' if files != 1 else ''}."

    def format_full(self) -> str:
        if self.ok:
            return self.summary()
        lines = [self.summary()]
        # Group by file
        seen: dict[str, list[Violation]] = {}
        for v in self.violations:
            seen.setdefault(v.path, []).append(v)
        for path, vs in sorted(seen.items()):
            kind = vs[0].file_kind
            lines.append(f"\n[{kind.upper()}] {path}")
            lines.extend(str(v) for v in vs)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _classify(path: str, base: str) -> str:
    rel = path.replace(base + os.sep, "").replace(base + "/", "")
    parts = rel.lower().split(os.sep)
    fname = os.path.basename(path)
    if fname == "agent.yaml":
        return "agent"
    if "overlay" in parts:
        return "overlay"
    if "flavour" in parts:
        return "flavour"
    if "infra" in parts:
        return "infra"
    if fname in ("mas.yaml", "mas.json"):
        return "mas"
    if "config" in parts:
        return "config"
    return "other"


# ---------------------------------------------------------------------------
# Core checker
# ---------------------------------------------------------------------------


def _check_file(path: str, base: str) -> List[Violation]:
    rel = path.replace(base + os.sep, "").replace(base + "/", "")
    kind = _classify(path, base)

    try:
        lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []

    violations: List[Violation] = []
    for i, ln in enumerate(lines):
        stripped = ln.strip()
        if stripped.startswith("#"):
            continue

        # Rule 1: model key in non-agent manifests
        if kind in ("flavour", "overlay", "infra", "mas", "config"):
            if _MODEL_LINE.match(ln) and "null" not in ln:
                violations.append(Violation(rel, kind, i + 1, "MODEL_IN_NON_AGENT", stripped))

        # Rule 2: access config in non-flavour files
        if kind in ("agent", "overlay", "mas"):
            if _ACCESS_LINE.match(ln) and "null" not in ln and '""' not in ln:
                violations.append(Violation(rel, kind, i + 1, "ACCESS_IN_NON_FLAVOUR", stripped))

    return violations


def run_hygiene_check(
    workspace: str | Path,
    search_roots: Optional[List[str]] = None,
) -> HygieneReport:
    """Run config hygiene checks over all YAML/JSON manifests in *workspace*.

    Parameters
    ----------
    workspace:
        Absolute path to the mas-framework workspace root.
    search_roots:
        Subdirectories to scan.  Defaults to ``_SEARCH_ROOTS``.

    Returns
    -------
    HygieneReport with all violations found.
    """
    base = str(Path(workspace).resolve())
    roots = search_roots or _SEARCH_ROOTS
    report = HygieneReport(workspace=base)

    for root_rel in roots:
        root_abs = os.path.join(base, root_rel)
        if not os.path.isdir(root_abs):
            continue
        for dirpath, dirs, files in os.walk(root_abs):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for fn in files:
                if fn.endswith((".yaml", ".json")):
                    full = os.path.join(dirpath, fn)
                    report.violations.extend(_check_file(full, base))

    return report
