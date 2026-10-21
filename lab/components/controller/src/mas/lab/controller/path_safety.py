#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Name and path guards for every controller write."""

from __future__ import annotations

import re
from pathlib import Path

from mas.lab.controller.kinds import NAME_PATTERN, VERSION_PATTERN

_NAME_RE = re.compile(NAME_PATTERN)
_VERSION_RE = re.compile(VERSION_PATTERN)


class UnsafeNameError(ValueError):
    """A user-supplied name, version, or item id failed the allow-list."""


def validate_name(name: str) -> str:
    text = str(name or "").strip()
    if not text or not _NAME_RE.fullmatch(text):
        raise UnsafeNameError(f"invalid name {name!r}")
    if text.startswith("."):
        raise UnsafeNameError(f"hidden name {name!r} is not allowed")
    if ".." in text or "/" in text or "\\" in text:
        raise UnsafeNameError(f"path-like name {name!r} is not allowed")
    return text


def validate_version(version: str | None) -> str | None:
    if version is None or str(version).strip() == "":
        return None
    text = str(version).strip()
    if not _VERSION_RE.fullmatch(text):
        raise UnsafeNameError(f"invalid version {version!r}")
    return text


def parse_item_id(item_id: str) -> tuple[str, str | None]:
    """Split ``name`` or ``name@version``. Reject path-like ids."""
    text = str(item_id or "").strip()
    if not text or text.startswith(".") or ".." in text or "/" in text or "\\" in text:
        raise UnsafeNameError(f"invalid item id {item_id!r}")
    if "@" not in text:
        return validate_name(text), None
    name, _, version = text.rpartition("@")
    return validate_name(name), validate_version(version)


def ensure_inside(root: Path, target: Path) -> Path:
    """Resolve *target* and require it to stay under *root* (no symlink escape)."""
    root_resolved = root.resolve()
    target_resolved = target.resolve()
    try:
        target_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise UnsafeNameError("path is outside the scope root") from exc
    return target_resolved


def is_hidden_under(root: Path, target: Path) -> bool:
    """True when any relative part is a dot-directory (staging leftovers, ``.git``)."""
    try:
        rel = target.resolve().relative_to(root.resolve())
    except ValueError:
        return True
    return any(part.startswith(".") for part in rel.parts)
