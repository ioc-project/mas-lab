#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Lab workspace helpers — project ``config.yaml`` accessors (not runtime shims)."""


import os
from pathlib import Path
from typing import Any

from mas.ctl.workspace.config import WorkspaceConfig, _find_workspace_file

from mas.runtime.constants import WORKSPACE_CONFIG_FILENAME

_CONFIG_FILE = WORKSPACE_CONFIG_FILENAME


def workspace_get(ws: WorkspaceConfig, tool: str, key: str, fallback: Any = None) -> Any:
    return (getattr(ws, "_data", {}) or {}).get(tool, {}).get(key, fallback)


def workspace_resolve_path(ws: WorkspaceConfig, tool: str, key: str) -> Path | None:
    raw = workspace_get(ws, tool, key)
    root = getattr(ws, "_path", None) or getattr(ws, "root", None)
    if raw is None or root is None:
        return None
    p = Path(str(raw)).expanduser()
    if p.is_absolute():
        return p
    return (Path(root) / p).resolve()


_find_config = _find_workspace_file


def find_workspace_root(start: Path | None = None) -> Path | None:
    """Find workspace root by walking up to ``config.yaml``."""
    current = (start or Path.cwd()).resolve()
    for directory in [current, *current.parents]:
        if (directory / _CONFIG_FILE).is_file():
            return directory
    return None


def project_workspace_root(start: Path | None = None) -> Path | None:
    """Project folder the controller should list from.

    ``MAS_WORKSPACE_ROOT`` / ``mas-lab serve --workspace`` wins even when
    that folder has no ``config.yaml``. Otherwise walk from *start* (cwd)
    for a project ``config.yaml``, stopping at ``.git``. User
    ``$XDG_CONFIG_HOME/mas/config.yaml`` is not a project pin — it must
    not shrink or flood the catalog.
    """
    env = os.environ.get("MAS_WORKSPACE_ROOT", "").strip()
    if env:
        path = Path(env).expanduser().resolve()
        return path if path.is_dir() else None
    current = (start or Path.cwd()).resolve()
    for directory in [current, *current.parents]:
        if (directory / _CONFIG_FILE).is_file():
            return directory
        if (directory / ".git").exists():
            break
    return None


__all__ = [
    "WorkspaceConfig",
    "find_workspace_root",
    "project_workspace_root",
    "workspace_get",
    "workspace_resolve_path",
    "_find_config",
    "_find_workspace_file",
]
