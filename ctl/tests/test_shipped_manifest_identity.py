#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shipped Agent/MAS manifests declare identity; validate would reject a path."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mas.ctl.validate import validate_file
from mas.ctl.validate.identity import collect_identity_issues

_ROOT = Path(__file__).resolve().parents[2]
_SKIP_PARTS = {".venv", ".venv-test", "node_modules", "ui", "site", "__pycache__"}
_SHIPPED_ROOTS = (
    _ROOT / "docs" / "tutorials",
    _ROOT / "docs" / "schemas" / "examples",
    _ROOT / "library-samples",
    _ROOT / "library-standard" / "examples",
    _ROOT / "library-skills" / "examples",
    _ROOT / "library-telemetry" / "overlays",
    _ROOT / "library-telemetry" / "pipelines",
    _ROOT / "library-eval" / "examples",
    _ROOT / "library-ioa",
    _ROOT / "labs",
)


def _discover(kind: str) -> list[Path]:
    paths: list[Path] = []
    for root in _SHIPPED_ROOTS:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.yaml")):
            if any(part in _SKIP_PARTS for part in path.parts):
                continue
            try:
                doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(doc, dict) and doc.get("kind") == kind:
                paths.append(path)
    return paths


_AGENT_PATHS = _discover("Agent")
_MAS_PATHS = _discover("MAS")


@pytest.mark.parametrize(
    "path",
    _AGENT_PATHS,
    ids=lambda p: str(p.relative_to(_ROOT)),
)
def test_shipped_agent_declares_identity(path: Path) -> None:
    pytest.importorskip("jsonschema")
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    issues = collect_identity_issues(doc, "agent")
    assert not issues, f"{path.relative_to(_ROOT)}: {[i.message for i in issues]}"
    result = validate_file(path, kind="agent", strict=True, resolve_refs=False)
    identity_errors = [
        issue
        for issue in result.issues
        if issue.level == "error"
        and ("metadata.name" in issue.message or "filesystem path" in issue.message)
    ]
    assert not identity_errors, identity_errors


@pytest.mark.parametrize(
    "path",
    _MAS_PATHS,
    ids=lambda p: str(p.relative_to(_ROOT)),
)
def test_shipped_mas_declares_identity(path: Path) -> None:
    pytest.importorskip("jsonschema")
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    issues = collect_identity_issues(doc, "mas")
    assert not issues, f"{path.relative_to(_ROOT)}: {[i.message for i in issues]}"
    result = validate_file(path, kind="mas", strict=True, resolve_refs=False)
    identity_errors = [
        issue
        for issue in result.issues
        if issue.level == "error"
        and ("metadata.name" in issue.message or "filesystem path" in issue.message)
    ]
    assert not identity_errors, identity_errors


def test_shipped_events_to_otel_steps_do_not_use_service_name() -> None:
    failures: list[str] = []
    for root in _SHIPPED_ROOTS:
        if not root.is_dir():
            continue
        for path in root.rglob("*.yaml"):
            if any(part in _SKIP_PARTS for part in path.parts):
                continue
            try:
                doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(doc, dict):
                continue
            issues = collect_identity_issues(doc, str(doc.get("kind") or "").lower())
            bad = [i for i in issues if "service_name is not MAS identity" in i.message]
            if bad:
                failures.append(f"{path.relative_to(_ROOT)}: {[i.message for i in bad]}")
    assert not failures, "\n".join(failures)
