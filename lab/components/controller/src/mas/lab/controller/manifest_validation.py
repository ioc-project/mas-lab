#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""In-process manifest validation for controller HTTP routes (no CLI subprocess)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from mas.ctl.validate.schemas import declared_kind, schema_path_for_kind
from mas.ctl.validate.validator import validate_data
from mas.runtime.secrets.errors import SecretRefError
from mas.runtime.secrets.manifest import check_manifest
from mas.runtime.spec.yaml_errors import YAML_ERROR_PREFIX, YamlLoadError, safe_yaml_load


def _document(content: str) -> Any:
    """The request document, or a 422 ``YAML does not parse: ...`` (position only, never the document)."""
    try:
        return safe_yaml_load(content)
    except YamlLoadError as exc:
        raise HTTPException(status_code=422, detail=f"{YAML_ERROR_PREFIX} {exc}") from None


def _refuse(doc: Any, *, source: str) -> None:
    try:
        check_manifest(doc, source=source)
    except SecretRefError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


def refuse_secret_values(content: str, *, source: str = "request") -> Any:
    """The request document; a 422 when it holds a secret value or a credential in a URL, or cannot be
    read (so nothing unchecked is ever written or run). The detail names the field, never the value."""
    doc = _document(content)
    _refuse(doc, source=source)
    return doc


def refuse_secret_fields(fields: Mapping[str, Any], *, source: str = "request") -> None:
    """Raise a 422 when a plain request field (a name, path, URL or nested reference) holds a secret
    field with a value or a credential in a URL. The detail names the field, never the value."""
    _refuse(dict(fields), source=source)


def refuse_secret_text(text: str, *, field: str, source: str = "request") -> None:
    """Raise a 422 when kept free text (a description) holds a secret: text that reads as YAML is checked
    as the data it holds (``api_key: sk-...``), other text as one string."""
    try:
        doc = safe_yaml_load(text)
    except YamlLoadError:
        doc = text
    _refuse({field: doc}, source=source)


def validate_manifest_yaml_content(
    manifest_yaml: str,
    *,
    base_dir: Path,
    kind: str | None = None,
    resolve_refs: bool = True,
) -> dict[str, Any]:
    """Validate inline YAML; return legacy CLI-shaped dict for API compatibility.

    YAML that cannot be read, or holds a secret value, is refused (422) as :func:`refuse_secret_values` does.
    """
    data = refuse_secret_values(manifest_yaml)

    if not isinstance(data, dict):
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": "manifest must be a YAML mapping",
            "command": "validate_data",
            "valid": False,
        }

    resolved_kind = kind or declared_kind(data)
    if resolved_kind is None or schema_path_for_kind(resolved_kind) is None:
        return {
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "command": "validate_data",
            "valid": True,
        }

    result = validate_data(
        data,
        source="<inline>",
        kind=resolved_kind,
        base_dir=base_dir,
        resolve_refs=resolve_refs,
    )

    errors = [i for i in result.issues if i.level == "error"]
    warnings = [i for i in result.issues if i.level == "warning"]
    lines = [f"[{i.level}] {i.message}" for i in errors + warnings]
    stderr = "\n".join(lines)

    return {
        "exit_code": 0 if result.ok else 1,
        "stdout": "",
        "stderr": stderr,
        "command": "validate_data",
        "valid": result.ok,
        "issues": [{"level": i.level, "message": i.message, "path": i.path} for i in result.issues],
    }


def validate_overlay_yaml_content(content: str, *, base_dir: Path) -> list[str] | None:
    """Validate overlay YAML; return error strings or None when valid."""
    outcome = validate_manifest_yaml_content(
        content,
        base_dir=base_dir,
        kind="overlay",
        resolve_refs=False,
    )
    if outcome.get("valid"):
        return None
    stderr = str(outcome.get("stderr") or "").strip()
    if stderr:
        return [line.strip() for line in stderr.splitlines() if line.strip()]
    return ["Validation failed"]
