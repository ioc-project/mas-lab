#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Resolve the PyPI distribution that shipped a given module or file.

Wheel names live in each package's ``pyproject.toml`` ``[project].name``.
Application code and install hints should call these helpers instead of
hardcoding that string.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import re
from functools import lru_cache
from pathlib import Path

_PROJECT_NAME_RE = re.compile(r"(?m)^name\s*=\s*[\"']([^\"']+)[\"']")


def name_for(*, file: str | Path | None = None, module: str | None = None) -> str:
    """Return the PyPI name of the wheel that owns *file* or *module*."""
    path = _anchor(file=file, module=module)
    found = _name_from_pyproject(path) or _name_from_installed(path)
    if not found:
        raise LookupError(f"no distribution name for {path}")
    return found


def extra_spec(extra: str, *, file: str | Path | None = None, module: str | None = None) -> str:
    """Return ``{distribution}[{extra}]`` for pip/uv."""
    extra = extra.strip().strip("[]")
    if not extra:
        raise ValueError("extra must be a non-empty extra name")
    return f"{name_for(file=file, module=module)}[{extra}]"


def extra_hint(
    extra: str,
    *,
    file: str | Path | None = None,
    module: str | None = None,
    editable: bool = False,
) -> str:
    """Return a ``uv pip install`` line for *extra* of the owning wheel."""
    spec = extra_spec(extra, file=file, module=module)
    flag = "-e " if editable else ""
    return f'uv pip install {flag}"{spec}"'


def installed_version(
    *,
    file: str | Path | None = None,
    module: str | None = None,
    missing: str = "0.1.0",
) -> str:
    """Installed version of the wheel that owns *file* or *module*."""
    try:
        return importlib.metadata.version(name_for(file=file, module=module))
    except importlib.metadata.PackageNotFoundError:
        return missing


def expand_extra(
    extra: str,
    *,
    file: str | Path | None = None,
    module: str | None = None,
) -> str:
    """Turn a bare extra name into a pip spec; leave a full spec unchanged."""
    extra = extra.strip()
    if not extra or "[" in extra:
        return extra
    try:
        return extra_spec(extra, file=file, module=module)
    except LookupError:
        return extra


def _anchor(*, file: str | Path | None, module: str | None) -> Path:
    if file is not None:
        return Path(file).resolve()
    if module:
        origin = _origin_of(module)
        if origin is not None:
            return origin
    raise LookupError("name_for requires file= or module=")


def _origin_of(module: str) -> Path | None:
    try:
        spec = importlib.util.find_spec(module)
    except Exception:
        return None
    if spec is None:
        return None
    if spec.origin and spec.origin != "namespace":
        return Path(spec.origin).resolve()
    locations = list(spec.submodule_search_locations or [])
    if locations:
        return Path(locations[0]).resolve()
    return None


def _name_from_pyproject(path: Path) -> str | None:
    here = path if path.is_dir() else path.parent
    for parent in [here, *here.parents]:
        toml = parent / "pyproject.toml"
        if not toml.is_file():
            continue
        name = _project_name(toml.read_text(encoding="utf-8"))
        if name:
            return name
    return None


def _project_name(text: str) -> str | None:
    idx = text.find("[project]")
    if idx < 0:
        return None
    rest = text[idx + len("[project]") :]
    nxt = rest.find("\n[")
    section = rest if nxt < 0 else rest[:nxt]
    match = _PROJECT_NAME_RE.search(section)
    return match.group(1) if match else None


@lru_cache(maxsize=256)
def _name_from_installed(path: Path) -> str | None:
    path = path.resolve()
    for dist in importlib.metadata.distributions():
        files = dist.files
        if not files:
            continue
        for entry in files:
            try:
                located = Path(dist.locate_file(entry)).resolve()
            except Exception:
                continue
            if located == path:
                return dist.metadata["Name"]
    return None
