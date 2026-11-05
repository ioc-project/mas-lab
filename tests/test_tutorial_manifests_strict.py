#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Strict validation gate for mas-lab tutorial runtime manifests."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.ctl.testing.cli_helpers import mas_lab_root
from mas.ctl.validate import validate_file

_RUNTIME_GLOBS: list[tuple[str, str]] = [
    ("**/agent.yaml", "agent"),
    ("**/overlays/*.yaml", "overlay"),
    ("**/mas.yaml", "mas"),
    ("**/deployments/*.yaml", "deployment"),
    ("**/tools/*.yaml", "tool"),
    ("**/infra/*.yaml", "infra"),
]


def _tutorial_runtime_paths() -> list[tuple[Path, str]]:
    root = mas_lab_root()
    if not root.is_dir():
        pytest.skip("mas-lab not present", allow_module_level=True)
    tutorials = root / "docs" / "tutorials"
    if not tutorials.is_dir():
        pytest.skip("docs/tutorials missing", allow_module_level=True)
    out: list[tuple[Path, str]] = []
    for pattern, kind in _RUNTIME_GLOBS:
        for path in sorted(tutorials.glob(pattern)):
            out.append((path, kind))
    return out


@pytest.mark.parametrize(
    "path,kind",
    _tutorial_runtime_paths(),
    ids=lambda x: str(x.relative_to(mas_lab_root())) if isinstance(x, Path) else x,
)
def test_tutorial_runtime_manifest_strict(path: Path, kind: str):
    """Core tutorial YAML (agent/overlay/mas/deployment/tool/infra) validates strictly."""
    pytest.importorskip("jsonschema")
    result = validate_file(path, kind=kind, strict=True, resolve_refs=False)
    assert result.ok, result.issues
