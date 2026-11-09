#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Workspace config discovery for mas-runtime (no mas-ctl import).

Project ``config.yaml`` at the repo root, then ``$XDG_CONFIG_HOME/mas/config.yaml``.
The legacy workspace filename is not read (see ``LEGACY_WORKSPACE_CONFIG_FILENAME``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from mas.runtime.constants import WORKSPACE_CONFIG_FILENAME
from mas.runtime.xdg import mas_user_config_file

_CONFIG_FILE = WORKSPACE_CONFIG_FILENAME


def library_search_paths(raw: Any) -> list[str]:
    """Search folders from ``manifest_libraries`` YAML.

    A list of paths is the real form: each entry is a folder we scan for
    ``library.yaml`` (the folder itself, or its immediate children). A
    legacy ``name: path`` map is flattened to its path values; names in
    that map are not aliases.
    """
    if isinstance(raw, list):
        return [item.strip() for item in raw if isinstance(item, str) and item.strip()]
    if isinstance(raw, dict):
        return [str(v).strip() for v in raw.values() if v]
    return []


def normalize_manifest_libraries(raw: Any) -> dict[str, str]:
    """Legacy ``{name: path}`` view of ``manifest_libraries``.

    Prefer :func:`library_search_paths` plus
    :func:`mas.library_roots.iter_libraries_in_search_path`. Names come
    from found directory stems, not from this map.
    """
    if isinstance(raw, list):
        out: dict[str, str] = {}
        for item in raw:
            if isinstance(item, str) and item.strip():
                name = Path(item).name
                if name and name not in {".", ".."}:
                    out[name] = item.strip()
        return out
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items() if v}
    return {}


def _user_config_path() -> Path:
    """Canonical user config path (XDG)."""
    return mas_user_config_file()


def resolve_config_relative(raw: str, config_file: Path) -> Path:
    """Resolve a path value from YAML relative to the config file's directory."""
    p = Path(raw).expanduser()
    if not p.is_absolute():
        return (config_file.parent / p).resolve()
    return p.resolve()


def find_workspace_file(start: Path | None = None) -> Path | None:
    """Return the active workspace config file path, or ``None`` if none exists."""
    env_root = os.environ.get("MAS_WORKSPACE_ROOT")
    if env_root:
        root = Path(env_root).expanduser().resolve()
        candidate = root / _CONFIG_FILE
        return candidate if candidate.is_file() else None

    current = (start or Path.cwd()).resolve()
    for directory in [current, *current.parents]:
        candidate = directory / _CONFIG_FILE
        if candidate.is_file():
            return candidate
        if (directory / ".git").exists():
            break

    user_config = _user_config_path()
    return user_config if user_config.is_file() else None


@dataclass
class RuntimeWorkspaceConfig:
    """Subset of workspace fields needed by mas-runtime."""

    _data: dict[str, Any] = field(default_factory=dict)
    _path: Path | None = None
    _config_file: Path | None = None

    @classmethod
    def load(cls, start: Path | None = None) -> RuntimeWorkspaceConfig:
        path = find_workspace_file(start)
        if path is None:
            return cls({})
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return cls({})
        root = path.parent
        return cls(
            data if isinstance(data, dict) else {},
            root,
            path,
        )

    @property
    def found(self) -> bool:
        return self._path is not None

    @property
    def root(self) -> Path | None:
        return self._path

    @property
    def config_path(self) -> Path | None:
        """Path to the YAML file loaded at :meth:`load` time (if any)."""
        return self._config_file

    @property
    def paths(self) -> dict[str, str]:
        raw = self._data.get("paths") or {}
        return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}

    @property
    def manifest_libraries(self) -> dict[str, str]:
        """Library roots from workspace config.

        Preferred form is a **list of paths**; the library name is the
        directory stem (from ``library.yaml`` / the folder). A legacy
        ``name: path`` map is still accepted but is not an alias table.

        For a **list** input, :func:`normalize_manifest_libraries` keys the
        result by each entry's trailing directory name, so two search-path
        entries that share a trailing directory name collapse into one —
        this property is lossy in that case. Callers that need every
        search-path entry (e.g. duplicate-stem library search paths) should
        use :attr:`raw_manifest_libraries` instead.
        """
        return normalize_manifest_libraries(self._data.get("manifest_libraries"))

    @property
    def raw_manifest_libraries(self) -> Any:
        """The ``manifest_libraries`` YAML value, unmodified.

        Unlike :attr:`manifest_libraries`, this never collapses list entries
        that happen to share a trailing directory name — pass it to
        :func:`library_search_paths` to get every search-path entry back.
        """
        return self._data.get("manifest_libraries")

    @property
    def aliases(self) -> dict[str, str]:
        raw = self._data.get("aliases") or {}
        return {str(alias): str(urn) for alias, urn in raw.items()} if isinstance(raw, dict) else {}

    @property
    def defaults(self) -> dict[str, str]:
        """Raw ``defaults:`` overrides from ``config.yaml`` (e.g. model, design_pattern)."""
        raw = self._data.get("defaults") or {}
        return {str(k): str(v) for k, v in raw.items() if v is not None} if isinstance(raw, dict) else {}

    @property
    def default_model(self) -> str | None:
        model = self.defaults.get("model")
        return model or None
