#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Separation rules on runtime manifests shipped in-repo (tutorials, labs, samples)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from mas.ctl.validate.separation import (
    AgentSeparationValidator,
    MASSeparationValidator,
    OverlaySeparationValidator,
)

_ROOT = Path(__file__).resolve().parents[1]

_SEARCH_ROOTS = (
    _ROOT / "docs" / "tutorials",
    _ROOT / "labs",
    _ROOT / "library-samples",
    _ROOT / "library-standard" / "examples",
    _ROOT / "tests" / "fixtures",
)

_AGENT_GLOBS = ("**/agent.yaml", "**/agents/**/agent.yaml")
_MAS_GLOBS = ("**/mas.yaml",)
_OVERLAY_GLOBS = ("**/overlays/*.yaml",)


def _yaml_doc(path: Path) -> dict | None:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    return data if isinstance(data, dict) else None


def _collect_cases(kind: str) -> list[tuple[str, Path, dict]]:
    globs = {
        "agent": _AGENT_GLOBS,
        "mas": _MAS_GLOBS,
        "overlay": _OVERLAY_GLOBS,
    }[kind]
    validator_kind = {
        "agent": "agent",
        "mas": "mas",
        "overlay": "overlay",
    }[kind]
    expected_kind = {
        "agent": "agent",
        "mas": "mas",
        "overlay": "overlay",
    }[kind]
    out: list[tuple[str, Path, dict]] = []
    for root in _SEARCH_ROOTS:
        if not root.is_dir():
            continue
        for pattern in globs:
            for path in sorted(root.glob(pattern)):
                doc = _yaml_doc(path)
                if doc is None:
                    continue
                if str(doc.get("kind") or "").lower() != expected_kind:
                    continue
                out.append((validator_kind, path, doc))
    return out


def _all_cases() -> list[tuple[str, Path, dict]]:
    cases: list[tuple[str, Path, dict]] = []
    for kind in ("agent", "mas", "overlay"):
        cases.extend(_collect_cases(kind))
    return cases


_CASES = _all_cases()


@pytest.mark.parametrize(
    "validator_kind,path,doc",
    _CASES,
    ids=[str(p.relative_to(_ROOT)) for _, p, _ in _CASES],
)
def test_shipped_runtime_manifest_separation(validator_kind: str, path: Path, doc: dict):
    validator = {
        "agent": AgentSeparationValidator,
        "mas": MASSeparationValidator,
        "overlay": OverlaySeparationValidator,
    }[validator_kind]
    violations = validator.collect_violations(doc)
    assert not violations, f"{path.relative_to(_ROOT)}: {violations}"
