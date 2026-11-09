#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Resolve a Control ref to a file; refuse absolute paths and directory escape."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml
from mas.ctl.session.flavour_control.error import ControlError
from mas.ctl.session.flavour_control.spec import GOVERNANCE, OBSERVABILITY, PLUGINS
from mas.runtime.spec.source import load_yaml_mapping

_URL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
ABSOLUTE_HINT = "use a path relative to the file that holds it, or pkg://"
STAY_UNDER = "must stay under the directory that holds it"


def as_path(value: Path | str) -> Path:
    return value if isinstance(value, Path) else Path(value)


def read_mapping(path: Path, *, what: str) -> dict[str, Any]:
    """YAML mapping from *path*, or a ControlError that names the file."""
    try:
        return load_yaml_mapping(path)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        raise ControlError(f"{what} {str(path)!r} cannot be read: {exc}") from None


def ref_walks_up(text: str) -> bool:
    """True when *text* contains a ``..`` path segment (``../x``, ``pkg://pkg/a/../b``)."""
    return ".." in Path(text.replace("\\", "/")).parts


def is_absolute_path(text: str) -> bool:
    """True for ``/x``, ``~/x``, ``C:\\x``, or ``file://``. Other URLs are not paths."""
    if text.lower().startswith("file:"):
        return True
    if _URL_SCHEME.match(text):
        return False
    return text.startswith("~") or Path(text).is_absolute() or bool(_WINDOWS_DRIVE.match(text))


def _path_findings(value: Any, where: str) -> list[tuple[str, str]]:
    """``(where, message)`` for every string under *value* that is an absolute path or walks up."""
    if isinstance(value, str):
        if is_absolute_path(value):
            return [(where, f"the absolute path {value!r}: a Control names plugins; {ABSOLUTE_HINT}")]
        if not _URL_SCHEME.match(value) and ref_walks_up(value):
            return [(where, f"the path {value!r} {STAY_UNDER}; {ABSOLUTE_HINT}")]
        return []
    if isinstance(value, dict):
        return [hit for key, item in value.items() for hit in _path_findings(item, f"{where}.{key}")]
    if isinstance(value, list):
        return [hit for index, item in enumerate(value) for hit in _path_findings(item, f"{where}[{index}]")]
    return []


def absolute_path_findings(data: dict[str, Any]) -> list[tuple[str, str]]:
    """``(where, message)`` for each unsafe path in a Control, or in a Flavour's ``spec.control`` refs."""
    kind = str(data.get("kind") or "")
    spec = data.get("spec") if isinstance(data.get("spec"), dict) else {}
    findings: list[tuple[str, str]] = []
    if kind == "Control":
        findings.extend(_path_findings(spec.get(OBSERVABILITY), f"spec.{OBSERVABILITY}"))
        findings.extend(_path_findings(spec.get(GOVERNANCE), f"spec.{GOVERNANCE}"))
        findings.extend(_path_findings(spec.get(PLUGINS), f"spec.{PLUGINS}"))
    elif kind == "Flavour" and isinstance(spec.get("control"), list):
        for index, row in enumerate(spec["control"]):
            ref = str(row.get("ref") or "") if isinstance(row, dict) else ""
            if ref and is_absolute_path(ref):
                findings.append((f"spec.control[{index}].ref", f"the absolute path {ref!r}; {ABSOLUTE_HINT}"))
            elif ref and ref_walks_up(ref):
                findings.append((f"spec.control[{index}].ref", f"the path {ref!r} {STAY_UNDER}; {ABSOLUTE_HINT}"))
    return findings


def control_search_dirs(*paths: Path | str | None) -> list[Path]:
    """Existing directories a Control ref is resolved against.

    Flavour file refs use the Flavour's directory only. CLI ``--control`` callers pass the current
    directory among the dirs; a relative Flavour ref never falls back to cwd by itself.
    """
    out: list[Path] = []
    for item in paths:
        if not item:
            continue
        directory = item if isinstance(item, Path) else Path(item)
        if directory.is_dir() and directory not in out:
            out.append(directory)
    return out


def require_control_file(path: Path, ref: str) -> Path:
    if path.is_file():
        return path.resolve()
    raise ControlError(f"Control ref {ref!r} is not a file")


def stay_under(path: Path, directory: Path, ref: str) -> None:
    if not path.is_relative_to(directory.resolve()):
        raise ControlError(f"Control ref {ref!r} {STAY_UNDER}; {ABSOLUTE_HINT}")


def find_control(ref: str, search_dirs: Sequence[Path], *, from_cli: bool = False) -> Path:
    """The Control file a ref names: pkg://, a library ref, or a relative path; an absolute path only from the CLI."""
    from mas.runtime.package_refs import resolve_path_ref, split_library_prefix

    text = ref.strip()
    if is_absolute_path(text):
        if not from_cli:
            raise ControlError(f"Control ref {ref!r} is an absolute path; {ABSOLUTE_HINT}")
        return require_control_file(Path(text).expanduser(), ref)
    packaged = text.startswith("pkg://") or split_library_prefix(text)[0] is not None
    if packaged:
        if ref_walks_up(text):
            raise ControlError(f"Control ref {ref!r} must stay inside the package; {ABSOLUTE_HINT}")
        try:
            resolved = resolve_path_ref(text, search_dirs[0] if search_dirs else Path.cwd())
        except (LookupError, ValueError, ModuleNotFoundError) as exc:
            raise ControlError(f"Control ref {ref!r} cannot be resolved: {exc}") from exc
        return require_control_file(resolved, ref)
    if ref_walks_up(text) and not from_cli:
        raise ControlError(f"Control ref {ref!r} {STAY_UNDER}; {ABSOLUTE_HINT}")
    for directory in search_dirs:
        nested = (directory / text).resolve()
        if not from_cli:
            stay_under(nested, directory, ref)
        if nested.is_file():
            return nested
    raise ControlError(f"Control ref {ref!r} is not a file")
