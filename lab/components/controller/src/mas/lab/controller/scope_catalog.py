#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Libraries and labs as first-class scopes (a lab is never a library)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from mas.lab.controller.kinds import LAB_KINDS, LIBRARY_BASE_KINDS
from mas.lab.controller.path_safety import UnsafeNameError, ensure_inside
from mas.runtime.constants import LAB_CONFIG_FILENAME, LIBRARY_MANIFEST_FILENAME
from mas.runtime.spec.source import load_yaml_file


def _title_from_slug(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").title()


def _description_of(path: Path) -> str:
    from mas.lab.controller.lab_registry import _library_description

    return _library_description(path)


def _is_lab_root(path: Path) -> bool:
    if path.name.endswith(".lab"):
        return True
    return (path / LAB_CONFIG_FILENAME).is_file() and not (path / LIBRARY_MANIFEST_FILENAME).is_file()


def _origin_from_source(source: str, *, path: Path) -> str:
    src = source or ""
    if src.startswith("entry-point"):
        return "package"
    if src.startswith("lab-local") or src.startswith("lab:"):
        return "lab"
    if "labs_search_paths" in src or "cwd-walk" in src or "labs_root" in src or "find_workspace_root" in src:
        return "lab" if _is_lab_root(path) else "workspace"
    if src.startswith("path:") or "MAS_LIBRARY_PATHS" in src:
        return "path"
    if src.startswith("manifest_libraries"):
        return "workspace"
    if _is_lab_root(path):
        return "lab"
    return "workspace"


@dataclass
class Scope:
    id: str
    kind: str
    name: str
    title: str
    description: str
    origin: str
    writable: bool
    root: Path
    libraries: list[str] = field(default_factory=list)
    owner: str | None = None
    kinds: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "origin": self.origin,
            "writable": self.writable,
            "kinds": list(self.kinds),
        }
        if self.libraries:
            payload["libraries"] = list(self.libraries)
        if self.owner:
            payload["owner"] = self.owner
        if self.counts:
            payload["counts"] = dict(self.counts)
        return payload


class ScopeCatalog:
    """Index of library and lab scopes derived from :class:`LabRegistry`."""

    def __init__(self, scopes: dict[str, Scope]):
        self._scopes = scopes

    def all(self) -> list[Scope]:
        def _sort_key(scope: Scope) -> tuple[str, str]:
            return (scope.kind, scope.name)

        return sorted(self._scopes.values(), key=_sort_key)

    def get(self, scope_id: str) -> Scope:
        scope = self._scopes.get(scope_id)
        if scope is None:
            # Allow a bare slug: try library first, then lab (old UI URLs).
            if ":" not in scope_id:
                for prefix in ("library:", "lab:"):
                    found = self._scopes.get(f"{prefix}{scope_id}")
                    if found is not None:
                        return found
            raise KeyError(scope_id)
        return scope

    def visible_libraries(self, scope_id: str) -> list[Scope]:
        """Libraries this scope may reference, in documented search order.

        Lab-local libraries first, then workspace, installed (package), path.
        """
        origin_order = {"lab": 0, "workspace": 1, "package": 2, "path": 3}
        scope = self.get(scope_id)
        libraries = [s for s in self.all() if s.kind == "library"]
        if scope.kind == "lab":
            owned = [s for s in libraries if s.owner == scope.id]
            others = [s for s in libraries if s.owner != scope.id]
            owned.sort(key=lambda s: s.name)
            others.sort(key=lambda s: (origin_order.get(s.origin, 9), s.name))
            return owned + others
        libraries.sort(key=lambda s: (origin_order.get(s.origin, 9), s.name))
        return libraries

    @classmethod
    def from_registry(
        cls,
        registry: Any,
        *,
        read_only_slugs: Iterable[str] | None = None,
    ) -> "ScopeCatalog":
        read_only = set(read_only_slugs or ())
        libraries: dict[str, Path] = dict(registry.library_paths())
        sources: dict[str, str] = dict(getattr(registry, "_library_sources", {}) or {})

        labs: list[Scope] = []
        lib_scopes: list[Scope] = []

        for slug, path in libraries.items():
            source = sources.get(slug, "")
            origin = _origin_from_source(source, path=path)
            if _is_lab_root(path):
                name = slug
                try:
                    doc = load_yaml_file(path / LAB_CONFIG_FILENAME) if (path / LAB_CONFIG_FILENAME).is_file() else {}
                    lab_block = (doc or {}).get("lab") or {}
                    name = str(lab_block.get("name") or slug)
                except Exception:
                    name = slug
                labs.append(
                    Scope(
                        id=f"lab:{name}",
                        kind="lab",
                        name=name,
                        title=_title_from_slug(name),
                        description=_description_of(path),
                        origin="lab",
                        writable=True,
                        root=path.resolve(),
                        kinds=list(LAB_KINDS),
                    )
                )
                continue
            # A lab root is never a library, even if someone added library.yaml.
            writable = origin != "package" and slug not in read_only
            lib_scopes.append(
                Scope(
                    id=f"library:{slug}",
                    kind="library",
                    name=slug,
                    title=_title_from_slug(slug),
                    description=_description_of(path),
                    origin=origin if origin != "lab" else "workspace",
                    writable=writable,
                    root=path.resolve(),
                    kinds=list(LIBRARY_BASE_KINDS),
                )
            )

        # Nested lab-local libraries (lab.libraries + immediate children with library.yaml).
        known_roots = {s.root for s in lib_scopes}
        for lab in labs:
            for child in _lab_local_libraries(lab.root):
                if child.resolve() in known_roots:
                    continue
                slug = child.name
                lib_scopes.append(
                    Scope(
                        id=f"library:{slug}",
                        kind="library",
                        name=slug,
                        title=_title_from_slug(slug),
                        description=_description_of(child),
                        origin="lab",
                        writable=True,
                        root=child.resolve(),
                        owner=lab.id,
                        kinds=list(LIBRARY_BASE_KINDS),
                    )
                )
                known_roots.add(child.resolve())

        for lib in lib_scopes:
            for lab in labs:
                try:
                    lib.root.relative_to(lab.root)
                except ValueError:
                    continue
                lib.origin = "lab"
                lib.owner = lab.id
                break

        by_id = {s.id: s for s in labs + lib_scopes}
        for lab in labs:
            lab.libraries = [
                s.id for s in lib_scopes if s.owner == lab.id
            ]
        return cls(by_id)


def _lab_local_libraries(lab_root: Path) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    config = lab_root / LAB_CONFIG_FILENAME
    if config.is_file():
        try:
            doc = load_yaml_file(config) or {}
        except Exception:
            doc = {}
        listed = ((doc.get("lab") or {}).get("libraries")) or []
        for rel in listed:
            try:
                child = ensure_inside(lab_root, lab_root / str(rel))
            except (UnsafeNameError, OSError):
                continue
            if (child / LIBRARY_MANIFEST_FILENAME).is_file() and child not in seen:
                found.append(child)
                seen.add(child)
    if lab_root.is_dir():
        for child in sorted(lab_root.iterdir()):
            if child.is_dir() and (child / LIBRARY_MANIFEST_FILENAME).is_file() and child.resolve() not in seen:
                found.append(child.resolve())
                seen.add(child.resolve())
    return found


def get_scope_catalog(store: Any | None = None) -> ScopeCatalog:
    """Build a catalog from the current manifest store / registry."""
    if store is None:
        from mas.lab.controller.deps import get_manifest_store

        store = get_manifest_store()
    registry = getattr(store, "_registry", None)
    if registry is None:
        from mas.lab.controller.lab_registry import get_lab_registry

        registry = get_lab_registry()
    read_only = getattr(store, "_read_only_slugs", None)
    catalog = ScopeCatalog.from_registry(registry, read_only_slugs=read_only)
    from mas.lab.controller.workspace_library import workspace_scope

    extra = workspace_scope()
    if extra is not None and extra.id not in catalog._scopes:
        catalog._scopes[extra.id] = extra
    return catalog
