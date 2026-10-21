#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Create lab scopes on a writable labs search path."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mas.lab.controller.item_service import ItemError, _catalog
from mas.lab.controller.path_safety import UnsafeNameError, validate_name
from mas.runtime.constants import LAB_CONFIG_FILENAME, LIBRARY_MANIFEST_FILENAME


def create_lab(
    *,
    name: str,
    description: str = "",
    local_library: bool = True,
    store: Any | None = None,
) -> Any:
    """Write ``{name}.lab`` under the first writable labs search path."""
    try:
        name = validate_name(name)
    except UnsafeNameError as exc:
        raise ItemError(400, str(exc)) from exc
    if store is None:
        from mas.lab.controller.deps import get_manifest_store

        store = get_manifest_store()
    catalog = _catalog(store)
    try:
        catalog.get(f"lab:{name}")
    except KeyError:
        pass
    else:
        raise ItemError(409, f"lab {name!r} already exists")
    parent = _labs_parent(store)
    lab_dir = parent / f"{name}.lab"
    if lab_dir.exists():
        raise ItemError(409, f"lab directory {lab_dir.name} already exists")
    lab_dir.mkdir(parents=True)
    (lab_dir / "experiments").mkdir()
    (lab_dir / "overlays").mkdir()
    (lab_dir / "datasets").mkdir()
    lib_rel = ""
    if local_library:
        lib_rel = f"{name.replace('-', '_')}_lab"
        lib_dir = lab_dir / lib_rel
        lib_dir.mkdir()
        (lib_dir / "apps").mkdir()
        (lib_dir / LIBRARY_MANIFEST_FILENAME).write_text(
            _library_manifest(lib_rel, name),
            encoding="utf-8",
        )
    (lab_dir / LAB_CONFIG_FILENAME).write_text(
        _lab_config(name, description, lib_rel),
        encoding="utf-8",
    )
    _register_created(store, name, lab_dir)
    return _catalog(store).get(f"lab:{name}")


def _labs_parent(store: Any) -> Path:
    registry = getattr(store, "_registry", None)
    search_paths: list[str] = []
    if registry is not None and hasattr(registry, "_labs_search_paths"):
        search_paths = list(registry._labs_search_paths())
    if not search_paths:
        search_paths = ["labs"]
    base = None
    if registry is not None and hasattr(registry, "_workspace_root"):
        base = registry._workspace_root()
    if base is not None:
        for entry in search_paths:
            path = Path(str(entry)).expanduser()
            if not path.is_absolute():
                path = base / path
            path = path.resolve()
            if path.name.endswith(".lab"):
                path = path.parent
            path.mkdir(parents=True, exist_ok=True)
            return path
    for scope in _catalog(store).all():
        if scope.kind == "lab" and scope.writable:
            return scope.root.parent
    fallback = Path.cwd() / "labs"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _register_created(store: Any, slug: str, lab_dir: Path) -> None:
    resolved = lab_dir.resolve()
    registry = getattr(store, "_registry", None)
    was_live = bool(getattr(store, "_refreshed", False)) and bool(
        getattr(registry, "_refreshed", False)
    )
    if registry is not None:
        libraries = getattr(registry, "_libraries", None)
        sources = getattr(registry, "_library_sources", None)
        if isinstance(libraries, dict):
            libraries[slug] = resolved
        if isinstance(sources, dict):
            sources[slug] = "created"
        if hasattr(registry, "invalidate_manifest_cache"):
            registry.invalidate_manifest_cache()
    store_libs = getattr(store, "_libraries", None)
    if isinstance(store_libs, dict):
        store_libs[slug] = resolved
    if was_live and hasattr(store, "refresh"):
        store.refresh()
    elif hasattr(store, "invalidate"):
        store.invalidate()


def _lab_config(name: str, description: str, library: str) -> str:
    lines = ["lab:", f"  name: {name}"]
    if description.strip():
        lines.append(f"  description: {json.dumps(description.strip())}")
    if library:
        lines.append("  libraries:")
        lines.append(f"    - {library}/")
    return "\n".join(lines) + "\n"


def _library_manifest(lib_name: str, lab_name: str) -> str:
    desc = json.dumps(f"Local library for {lab_name}")
    return (
        "apiVersion: mas/v1\n"
        "kind: Library\n"
        f"name: {lib_name}\n"
        f"description: {desc}\n"
    )
