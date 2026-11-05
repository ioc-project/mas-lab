#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""JSON Schema validation for OSS Agent manifests (tutorials, samples, examples, labs)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from mas.ctl.validate import validate_file

_ROOT = Path(__file__).resolve().parents[1]
_SKIP_PARTS = {".venv", ".venv-test", "node_modules", "ui", "site", "__pycache__"}
_AGENT_ROOTS = (
    _ROOT / "docs" / "tutorials",
    _ROOT / "library-samples",
    _ROOT / "library-standard" / "examples",
    _ROOT / "labs",
)


def _discover_agent_yaml() -> list[Path]:
    paths: list[Path] = []
    for root in _AGENT_ROOTS:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.yaml")):
            if any(part in _SKIP_PARTS for part in path.parts):
                continue
            try:
                doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(doc, dict) and doc.get("kind") == "Agent":
                paths.append(path)
    return paths


@pytest.mark.parametrize(
    "path",
    _discover_agent_yaml(),
    ids=lambda p: str(p.relative_to(_ROOT)),
)
def test_oss_agent_manifest_strict(path: Path) -> None:
    pytest.importorskip("jsonschema")
    result = validate_file(path, kind="agent", strict=True, resolve_refs=True)
    assert result.ok, result.issues
