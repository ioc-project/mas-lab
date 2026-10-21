#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Project ``config.yaml`` and XDG user config — v2 ctl discovery."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from mas.runtime.workspace_config import (
    _user_config_path,
    find_workspace_file,
    normalize_manifest_libraries,
    resolve_config_relative,
)
from mas.runtime.xdg import mas_cache_root

_ENV_INFRA_REFS = "MAS_INFRA_REFS"
_ENV_RUNTIME_REFS = "MAS_RUNTIME_REFS"


def infra_refs_from_env() -> list[str]:
    """Parse ``MAS_INFRA_REFS`` (comma- or space-separated bundle refs).

    Last-resort override of workspace ``infra_refs`` (CLI ``--infra-ref`` still
    wins); declare refs in ``config.yaml`` or pass ``--infra-ref`` instead.
    """
    raw = os.environ.get(_ENV_INFRA_REFS, "").strip()
    if not raw:
        return []
    parts = [p.strip() for p in raw.replace(",", " ").split() if p.strip()]
    return parts


def merge_infra_refs(
    *,
    workspace_refs: list[str],
    user_refs: list[str] | None = None,
    cli_refs: list[str],
    workspace_found: bool = False,
) -> list[str]:
    """Merge infra refs: workspace < user default < CLI (never agent/MAS manifests)."""
    seen: set[str] = set()
    ordered: list[str] = []
    user_part = [] if (workspace_found and workspace_refs) else list(user_refs or [])
    for ref in workspace_refs + user_part + cli_refs:
        if ref and ref not in seen:
            seen.add(ref)
            ordered.append(ref)
    return ordered


def runtime_refs_from_env() -> list[str]:
    """Parse ``MAS_RUNTIME_REFS`` (comma- or space-separated RuntimeEngine refs)."""
    raw = os.environ.get(_ENV_RUNTIME_REFS, "").strip()
    if not raw:
        return []
    parts = [p.strip() for p in raw.replace(",", " ").split() if p.strip()]
    return parts


def collect_mas_infra_refs(config: dict[str, Any]) -> list[str]:
    # Infrastructure is deployment configuration, supplied by workspace or CLI.
    # MAS/Agent specs deliberately do not carry infra references.
    return []


def merge_runtime_refs(
    *,
    workspace_refs: list[str],
    user_refs: list[str] | None = None,
    cli_refs: list[str],
    workspace_found: bool = False,
) -> list[str]:
    """Merge runtime refs: workspace < user default < CLI (additive, de-duplicated)."""
    seen: set[str] = set()
    ordered: list[str] = []
    user_part = [] if (workspace_found and workspace_refs) else list(user_refs or [])
    for ref in workspace_refs + user_part + cli_refs:
        if ref and ref not in seen:
            seen.add(ref)
            ordered.append(ref)
    return ordered


def collect_infra_interceptors(config: dict[str, Any]) -> list[str]:
    """Read deployment middleware from workspace/CLI configuration only."""
    return []


def merge_infra_interceptors(
    *,
    workspace_interceptors: list[str],
    cli_interceptors: list[str],
) -> list[str]:
    """Merge interceptor refs: workspace < CLI (additive, de-duplicated)."""
    seen: set[str] = set()
    ordered: list[str] = []
    for ref in workspace_interceptors + cli_interceptors:
        if ref and ref not in seen:
            seen.add(ref)
            ordered.append(ref)
    return ordered


@dataclass
class WorkspaceConfig:
    _data: dict[str, Any] = field(default_factory=dict)
    _path: Path | None = None
    _config_file: Path | None = None

    @classmethod
    def load(cls, start: Path | None = None) -> WorkspaceConfig:
        path = _find_workspace_file(start or Path.cwd())
        if path is None:
            return cls({})
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return cls({})
        return cls(
            data if isinstance(data, dict) else {},
            path.parent,
            path,
        )

    def with_cli_overrides(self, overrides: tuple[str, ...] | list[str]) -> WorkspaceConfig:
        """Return an in-memory workspace view with CLI patches applied."""
        from mas.ctl.overrides import apply_cli_overrides, overrides_for_root

        overrides = overrides_for_root(overrides, "workspace")
        if not overrides:
            return self

        return WorkspaceConfig(
            apply_cli_overrides(self._data, overrides, root="workspace"),
            self._path,
            self._config_file,
        )

    @property
    def found(self) -> bool:
        return self._path is not None

    @property
    def root(self) -> Path | None:
        return self._path

    @property
    def infra_refs(self) -> list[str]:
        raw = self._data.get("infra_refs") or []
        return [raw] if isinstance(raw, str) else list(raw)

    @property
    def effective_infra_refs(self) -> list[str]:
        """Workspace ``infra_refs`` with the last-resort ``MAS_INFRA_REFS`` override."""
        env_refs = infra_refs_from_env()
        if env_refs:
            from mas.ctl.env import warn_env_override

            warn_env_override(_ENV_INFRA_REFS, ",".join(env_refs), replaces="workspace infra_refs")
            return env_refs
        return self.infra_refs

    @property
    def runtime_refs(self) -> list[str]:
        raw = self._data.get("runtime_refs") or []
        return [raw] if isinstance(raw, str) else list(raw)

    @property
    def effective_runtime_refs(self) -> list[str]:
        """Workspace ``runtime_refs`` with the last-resort ``MAS_RUNTIME_REFS`` override."""
        env_refs = runtime_refs_from_env()
        if env_refs:
            from mas.ctl.env import warn_env_override

            warn_env_override(_ENV_RUNTIME_REFS, ",".join(env_refs), replaces="workspace runtime_refs")
            return env_refs
        return self.runtime_refs

    @property
    def infra_interceptors(self) -> list[str]:
        raw = self._data.get("infra_interceptors") or []
        return [raw] if isinstance(raw, str) else list(raw)

    @property
    def manifest_libraries(self) -> dict[str, str]:
        return normalize_manifest_libraries(self._data.get("manifest_libraries"))

    @property
    def raw_manifest_libraries(self) -> Any:
        """The ``manifest_libraries`` YAML value, unmodified.

        Unlike :attr:`manifest_libraries` (which, for a list input, keys the
        result by trailing directory name and so collapses two search-path
        entries that share one), this never loses an entry. Pass it to
        :func:`mas.runtime.workspace_config.library_search_paths` to get
        every search-path entry back.
        """
        return self._data.get("manifest_libraries")

    @property
    def default_model(self) -> str | None:
        defaults = self._data.get("defaults") or {}
        if isinstance(defaults, dict):
            model = defaults.get("model")
            return str(model) if model else None
        return None

    @property
    def plugins(self) -> list[str]:
        """Overlay plugin URNs from ``plugins:``. Empty when omitted or all commented."""
        from mas.runtime.workspace_plugins import parse_plugin_refs

        return parse_plugin_refs(self._data.get("plugins"))

    @property
    def mas_ctl(self) -> dict[str, Any]:
        raw = self._data.get("mas_ctl") or {}
        return dict(raw) if isinstance(raw, dict) else {}

    @property
    def cache_read_only(self) -> bool:
        """True when ``mas_ctl.cache: read-only`` makes recorded answers the default."""
        return str(self.mas_ctl.get("cache") or "").strip().lower() == "read-only"

    @property
    def paths(self) -> dict[str, str]:
        raw = self._data.get("paths") or {}
        return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}

    @property
    def config_path(self) -> Path | None:
        return self._config_file

    @property
    def infra_bundle_refs(self) -> list[str]:
        """Workspace infra bundle refs (InfraBundle / LLMProxy paths or library refs)."""
        return self.infra_refs

    @property
    def deployment_name(self) -> str | None:
        dep = self.mas_ctl.get("deployment")
        return str(dep) if dep else None

    @property
    def runtime_id(self) -> str | None:
        rid = self.mas_ctl.get("runtime_id") or self.mas_ctl.get("kernel")
        return str(rid) if rid else None

    @property
    def runtime_profile_path(self) -> Path | None:
        raw = self.mas_ctl.get("runtime_profile")
        if not raw or self._path is None:
            return None
        candidate = (self._path / str(raw)).resolve()
        return candidate if candidate.is_file() else None

    def resolve_library_path(self, lib_ref: str) -> Path | None:
        """Resolve ``name:path`` via the shared library-name lookup.

        Library roots come from lab-config paths, workspace
        ``manifest_libraries:``, then installed libraries (same order as
        :func:`mas.library_roots.resolve_named_library_root`). The remainder
        of the ref is a file under that root (``.yaml`` implied for bundle
        names).
        """
        if ":" not in lib_ref:
            return None
        lib, rest = lib_ref.split(":", 1)
        from mas.library_roots import resolve_named_library_root

        anchors = [p for p in (self._path, Path.cwd()) if p is not None]
        root = resolve_named_library_root(lib, *anchors)
        if root is None:
            return None
        candidate = (root / rest).with_suffix(".yaml")
        if candidate.is_file():
            return candidate
        if rest.endswith(".yaml") and (root / rest).is_file():
            return (root / rest).resolve()
        direct = root / rest
        if direct.is_file():
            return direct.resolve()
        return None


@dataclass
class UserConfig:
    default_infra: str | None = None
    default_runtime: str | None = None
    cache_dir: Path = field(default_factory=mas_cache_root)
    _data: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def mas_ctl(self) -> dict[str, Any]:
        raw = self._data.get("mas_ctl") or {}
        return dict(raw) if isinstance(raw, dict) else {}

    @classmethod
    def load(cls) -> UserConfig:
        config_path = _user_config_path()
        if not config_path.is_file():
            return cls(default_infra="standard:production")
        try:
            data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except Exception:
            return cls(default_infra="standard:production")
        if not isinstance(data, dict):
            data = {}
        cache = data.get("cache_dir")
        if not cache and isinstance(data.get("paths"), dict):
            cache = data["paths"].get("cache_dir")
        if cache:
            cache_dir = resolve_config_relative(str(cache), config_path)
        else:
            cache_dir = mas_cache_root()
        return cls(
            default_infra=data.get("default_infra"),
            default_runtime=data.get("default_runtime"),
            cache_dir=cache_dir,
            _data=data,
        )


def _find_workspace_file(start: Path) -> Path | None:
    return find_workspace_file(start)
