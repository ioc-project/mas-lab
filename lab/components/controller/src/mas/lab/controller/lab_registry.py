#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Unified lab registry — single point of contact for controller + UI discovery.

Extends the runtime object registry (``mas.registry``, plugin URN registry) with
lab-specific resources: libraries, experiments, pipelines, datasets, MAS apps,
and pipeline step types.
"""
from __future__ import annotations

import contextlib
import importlib.resources
import logging
import os
from functools import lru_cache
from importlib.metadata import entry_points as _entry_points
from pathlib import Path
from typing import Any, Dict, List, Optional

from mas.runtime.constants import (
    LAB_CONFIG_FILENAME,
    LIBRARY_MANIFEST_FILENAME,
    WORKSPACE_CONFIG_FILENAME,
)
from mas.runtime.spec.source import load_yaml_file

logger = logging.getLogger(__name__)


def _library_description(path: Path) -> str:
    for candidate in (path / LAB_CONFIG_FILENAME, path / "mas.yaml", path / "README.md"):
        if candidate.exists():
            try:
                if candidate.suffix == ".yaml":
                    data = load_yaml_file(candidate)
                    desc = (
                        data.get("description")
                        or data.get("metadata", {}).get("description")
                        or (data.get("lab") or {}).get("description")
                    )
                    if desc:
                        return str(desc).strip().splitlines()[0][:200]
                else:
                    return candidate.read_text(encoding="utf-8").splitlines()[0][:200]
            except Exception:
                pass
    return ""


def _manifest_library_names(path: Path) -> list[str]:
    """Directory stem plus ``library.yaml`` ``name`` / schemes (lookup aliases)."""
    names: list[str] = []
    stem = path.stem if path.name.endswith(".lab") else path.name
    if stem:
        names.append(stem)
    manifest = path / LIBRARY_MANIFEST_FILENAME
    if not manifest.is_file():
        return names
    try:
        data = load_yaml_file(manifest)
    except Exception:
        return names
    if not isinstance(data, dict):
        return names
    pkg = str(data.get("name") or "").strip()
    if pkg and pkg not in names:
        names.append(pkg)
    for key in ("schemes", "aliases"):
        raw = data.get(key) or []
        if not isinstance(raw, list):
            continue
        for item in raw:
            text = str(item).strip()
            if text and text not in names:
                names.append(text)
    return names


from mas.runtime.agent_defaults import (
    agent_defaults as _agent_defaults,
    resolve_default_model,
)

_YAML_SUFFIXES = frozenset({".yaml", ".yml"})
"""Directories that group manifests by role (path convention — filenames are free)."""
_TYPED_DIRS = frozenset({"datasets", "overlays", "flavours", "infra", "apps"})
_DISCOVERY_SKIP_DIRS = _TYPED_DIRS | {".git", "node_modules", "__pycache__", ".cache"}

_MAS_MANIFEST_NAMES = ("mas.yaml", "mas-bench.yaml")

_REGISTRY: Optional["LabRegistry"] = None


def _is_yaml_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in _YAML_SUFFIXES


def _iter_library_yaml(lib_dir: Path):
    """All YAML files under a library root (discovery walks content, not names)."""
    if not lib_dir.is_dir():
        return
    for path in sorted(lib_dir.rglob("*")):
        if _is_yaml_file(path):
            yield path


def _iter_manifest_files(lib_dir: Path, expected_kind: str, *, parent_dir: str | None = None):
    """Yield YAML files whose declared kind matches *expected_kind*."""
    seen: set[Path] = set()
    for path in _iter_library_yaml(lib_dir):
        rel_parts = path.relative_to(lib_dir).parts
        if parent_dir is not None:
            if parent_dir not in rel_parts:
                continue
        elif _DISCOVERY_SKIP_DIRS.intersection(rel_parts):
            continue
        try:
            doc = load_yaml_file(path)
        except Exception:
            continue
        from mas.ctl.validate.schemas import declared_kind

        if declared_kind(doc) != expected_kind:
            continue
        key = path.resolve()
        if key not in seen:
            seen.add(key)
            yield path


class LabRegistry:
    """Lab-facing registry: runtime objects + workspace libraries + lab artifacts."""

    def __init__(self, workspace: Any = None) -> None:
        self._workspace = workspace
        self._libraries: Dict[str, Path] = {}
        self._library_sources: Dict[str, str] = {}
        self._refreshed = False
        # Cache of classified manifest paths keyed by (lib_dir, expected_kind,
        # parent_dir): populated lazily on first access per library/kind and
        # reused for the lifetime of the current refresh() cycle. Without this,
        # every list_experiments()/list_pipelines()/list_datasets()/
        # list_overlays() call re-walked the entire library tree and re-parsed
        # every YAML file to reclassify its declared kind, even though the
        # result is invariant until the next explicit refresh().
        self._manifest_file_cache: Dict[tuple[Path, str, str | None], List[Path]] = {}
        eager = os.environ.get("MAS_LAB_REGISTRY_EAGER_REFRESH", "1").strip().lower()
        if eager not in {"0", "false", "no"}:
            self.refresh()

    def _ensure_ready(self) -> None:
        if self._libraries:
            return
        if not self._refreshed:
            self.refresh()

    def refresh(self) -> None:
        self._libraries, self._library_sources = self._discover_library_paths()
        self._manifest_file_cache = {}
        self._log_discovery()
        self._refreshed = True

    def invalidate_manifest_cache(self) -> None:
        """Drop cached experiment/pipeline/dataset/overlay file listings.

        The CRUD routes write/delete manifest files directly on the
        filesystem rather than through this registry, so they must call this
        after any such write so the next ``list_*`` call rescans instead of
        returning a listing cached from before the write.
        """
        self._manifest_file_cache = {}

    def _cached_manifest_files(
        self, lib_dir: Path, expected_kind: str, *, parent_dir: str | None = None
    ) -> List[Path]:
        """Scan-once wrapper around :func:`_iter_manifest_files`.

        The underlying scan (full ``rglob`` + YAML-parse-and-classify per file)
        is only ever performed once per (library, kind) pair for the current
        refresh() cycle; subsequent calls reuse the cached path list.
        """
        cache = self._manifest_file_cache
        key = (lib_dir, expected_kind, parent_dir)
        cached = cache.get(key)
        if cached is None:
            cached = list(_iter_manifest_files(lib_dir, expected_kind, parent_dir=parent_dir))
            cache[key] = cached
        return cached

    # ── Unified query API (UI / CLI / daemon) ─────────────────────────────
    #
    # ``spec_key`` matches agent manifest ``spec.<key>`` (e.g. design_pattern).
    # Runtime spec keys delegate to :func:`mas.runtime.registry.get_registry`.
    # Lab-only keys (pipeline_step, experiment, …) are resolved here.

    @staticmethod
    def _runtime_spec_keys() -> frozenset[str]:
        from mas.runtime.registry import get_registry

        return get_registry().runtime_spec_keys()

    def _runtime_registry(self):
        from mas.runtime.registry import get_registry

        return get_registry()

    def resolve(
        self,
        spec_key: str,
        binding: dict | None = None,
        *,
        manifest: dict | None = None,
    ) -> Any:
        """Resolve and instantiate ``spec.<spec_key>`` (runtime) or lab artifact."""
        if spec_key in self._runtime_spec_keys():
            return self._runtime_registry().create(spec_key, binding, manifest=manifest)
        raise KeyError(f"LabRegistry.resolve: unknown spec key {spec_key!r}")

    def list(self, spec_key: str | None = None) -> Any:
        """List plugins or artifacts. *spec_key* = manifest ``spec.<key>`` name."""
        if spec_key is None:
            return self.catalog()
        if spec_key in self._runtime_spec_keys():
            return self._runtime_registry().list(spec_key)
        if spec_key == "pipeline_step":
            return self.pipeline_step_types()
        raise KeyError(f"LabRegistry.list: unknown spec key {spec_key!r}")

    def runtime_objects(self, kind: str) -> Dict[str, Path]:
        """Objects from ``mas.<kind>s`` entry points (app, dataset, tool).

        Uses a single discovery pass (``list_objects``) rather than looping
        ``get(kind, name)`` per name — each call to ``get``/``list_names``
        independently re-walks every library root and re-parses every
        manifest of that kind, so a per-name loop turns an O(1) scan into an
        O(N) one (this was the actual source of multi-second discovery
        reports, not the directory walk itself).
        """
        try:
            from mas.registry import list_objects

            return list_objects(kind)
        except ImportError:
            return {}

    def list_runtime_object_names(self) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for kind in ("app", "dataset", "tool"):
            out[kind] = sorted(self.runtime_objects(kind))
        return out

    # ── Agent / infra defaults ────────────────────────────────────────────

    def default_model(self) -> str:
        """Workspace default model (logical default, not infra)."""
        return resolve_default_model(self._workspace)

    def agent_defaults(self) -> Dict[str, Any]:
        return _agent_defaults(self._workspace)

    # ── Libraries ───────────────────────────────────────────────────────

    def library_paths(self) -> Dict[str, Path]:
        self._ensure_ready()
        return dict(self._libraries)

    def libraries(self) -> List[Dict[str, str]]:
        self._ensure_ready()
        items: List[Dict[str, str]] = []
        for slug, path in sorted(self._libraries.items()):
            items.append(
                {
                    "dir": slug,
                    "name": slug.replace("-", " ").replace("_", " ").title(),
                    "description": _library_description(path),
                }
            )
        return items

    def library_root(self, library: str) -> Path:
        self._ensure_ready()
        root = self._libraries.get(library)
        if root is not None:
            return root
        for path in self._libraries.values():
            if path.name == library:
                return path
        try:
            from mas.library_roots import resolve_named_library_root

            found = resolve_named_library_root(library)
            if found is not None:
                return found
        except Exception:
            pass
        raise KeyError(f"Library {library!r} not found")

    def catalog(self) -> Dict[str, Any]:
        """Full registry snapshot for ``GET /api/registry``."""
        runtime = self._runtime_registry()
        spec_catalog = {
            key: runtime.list(key) for key in sorted(self._runtime_spec_keys())
        }
        return {
            "runtime": self.list_runtime_object_names(),
            "spec": spec_catalog,
            "pipeline_steps": self.pipeline_step_types(),
            "libraries": [lib["dir"] for lib in self.libraries()],
            "defaults": self.agent_defaults(),
        }

    # ── Library artifacts ───────────────────────────────────────────────

    def list_experiments(self, library: str) -> List[Dict[str, Any]]:
        root = self.library_root(library)
        return [self._parse_experiment_metadata(p, root) for p in self._iter_experiment_files(root)]

    def list_pipelines(self, library: str) -> List[Dict[str, Any]]:
        root = self.library_root(library)
        return [self._parse_pipeline_metadata(p, root) for p in self._iter_pipeline_files(root)]

    def list_datasets(self, library: str) -> List[Dict[str, str]]:
        root = self.library_root(library)
        datasets: List[Dict[str, str]] = []
        for path in self._iter_dataset_files(root):
            rel = str(path.relative_to(root))
            datasets.append(
                {
                    "name": path.name,
                    "path": rel,
                    "description": self._dataset_description(path),
                }
            )
        return datasets

    def list_overlays(self, library: str) -> List[Dict[str, str]]:
        root = self.library_root(library)
        overlays: List[Dict[str, str]] = []
        for path in self._iter_overlay_files(root):
            rel = str(path.relative_to(root))
            entry: Dict[str, str] = {
                "name": path.stem,
                "filename": path.name,
                "path": rel,
            }
            try:
                data = load_yaml_file(path)
                meta = data.get("metadata", {})
                entry["description"] = str(meta.get("description", "") or "")
                entry["namespace"] = str((data.get("metadata") or {}).get("namespace", "global") or "global")
            except Exception:
                entry["description"] = ""
                entry["namespace"] = "global"
            overlays.append(entry)
        return overlays

    def collect_mas_resources(self, library: str) -> Dict[str, Dict[str, Any]]:
        # Only discover apps from the library filesystem.  The mas.apps
        # entry-point registry is a separate discovery mechanism used by
        # CLI / experiment resolution; mixing it in here would create
        # duplicates and break CRUD operations that expect filesystem paths.
        root = self.library_root(library)
        return self._collect_mas_resources(root)

    def list_all_experiments(self) -> List[Dict[str, Any]]:
        """All experiment definitions across discovered libraries."""
        self._ensure_ready()
        items: List[Dict[str, Any]] = []
        for slug in sorted(self._libraries):
            for exp in self.list_experiments(slug):
                items.append({**exp, "library": slug})
        return items

    def discovery_report(self) -> Dict[str, Any]:
        """Snapshot of paths scanned and libraries resolved (for debugging / UI)."""
        self._ensure_ready()
        ws_root = self._workspace_root()
        return {
            "workspace_root": str(ws_root) if ws_root else None,
            "mas_workspace_root_env": os.environ.get("MAS_WORKSPACE_ROOT") or None,
            "cwd": str(Path.cwd()),
            "labs_search_paths": self._labs_search_paths(),
            "libraries": [
                {
                    "slug": slug,
                    "path": str(path),
                    "source": self._library_sources.get(slug, "unknown"),
                }
                for slug, path in sorted(self._libraries.items())
            ],
            "runtime_apps": sorted(self.runtime_objects("app")),
            "runtime_datasets": sorted(self.runtime_objects("dataset")),
        }

    def pipeline_step_types(self) -> Dict[str, Any]:
        try:
            from mas.runtime.registry import get_registry

            step_types = []
            categories: Dict[str, dict] = {}
            for item in get_registry().list("step"):
                shortcuts = item.get("shortcuts") or []
                step_id = str(shortcuts[0]) if shortcuts else str(item.get("urn") or "").rsplit(".", 1)[-1]
                if not step_id:
                    continue
                attrs = item.get("attributes") or {}
                category = str(attrs.get("category") or "general")
                step_types.append(
                    {
                        "type": step_id,
                        "label": step_id.replace("_", " ").title(),
                        "description": str(item.get("description") or ""),
                        "phase": str(attrs.get("phase") or "post"),
                        "category": category,
                        "requires": attrs.get("requires"),
                        "config": {},
                    }
                )
                categories.setdefault(
                    category, {"id": category, "label": category.title(), "color": "#888"}
                )
            return {"step_types": step_types, "categories": list(categories.values())}
        except Exception as exc:
            logger.debug("pipeline step types unavailable: %s", exc)
            return {"step_types": [], "categories": []}

    # ── Discovery internals ─────────────────────────────────────────────

    def _workspace_root(self) -> Optional[Path]:
        if self._workspace is None:
            try:
                from mas.lab.workspace import WorkspaceConfig

                ws = WorkspaceConfig.load()
                if ws.found and ws._path is not None:
                    return ws._path
            except Exception:
                return None
            return None
        base = getattr(self._workspace, "_path", None)
        return Path(base) if base is not None else None

    def _labs_search_paths(self) -> List[str]:
        if self._workspace is not None:
            mas_lab = getattr(self._workspace, "_data", {}).get("mas_lab") or {}
            paths = mas_lab.get("labs_search_paths")
            if paths:
                return [paths] if isinstance(paths, str) else list(paths)
            if hasattr(self._workspace, "mas_lab"):
                return list(self._workspace.mas_lab.labs_search_paths)
        try:
            from mas.lab.workspace import WorkspaceConfig

            ws = WorkspaceConfig.load()
            if ws.found:
                mas_lab = ws._data.get("mas_lab") or {}
                paths = mas_lab.get("labs_search_paths")
                if paths:
                    return [paths] if isinstance(paths, str) else list(paths)
        except Exception:
            pass
        return ["labs"]

    def _discover_library_paths(self) -> tuple[Dict[str, Path], Dict[str, str]]:
        libraries: Dict[str, Path] = {}
        sources: Dict[str, str] = {}

        def _add(slug: str, path: Path, source: str) -> None:
            resolved = path.resolve()
            if not resolved.exists():
                logger.debug("skip missing library %s at %s (%s)", slug, resolved, source)
                return
            if slug not in libraries:
                libraries[slug] = resolved
                sources[slug] = source
            else:
                logger.debug(
                    "library %s already mapped to %s; ignoring %s from %s",
                    slug,
                    libraries[slug],
                    resolved,
                    source,
                )

        for name, path in self.runtime_objects("app").items():
            _add(name, path, f"entry-point:mas.apps:{name}")

        try:
            from mas.library_roots import discover_library_roots

            for root in discover_library_roots():
                for slug in _manifest_library_names(root):
                    _add(slug, root, "manifest_libraries")
        except Exception as exc:
            logger.debug("manifest library discovery failed: %s", exc)

        for entry in os.environ.get("MAS_LIBRARY_PATHS", "").split(os.pathsep):
            entry = entry.strip()
            if not entry:
                continue
            path = Path(entry).expanduser()
            if not path.is_dir():
                continue
            if path.name.endswith(".lab") or (path / LAB_CONFIG_FILENAME).is_file():
                for slug in _manifest_library_names(path):
                    _add(slug, path, "MAS_LIBRARY_PATHS:lab")
            try:
                from mas.library_roots import iter_libraries_in_search_path

                libs = iter_libraries_in_search_path(path)
            except Exception:
                libs = [path] if (path / LIBRARY_MANIFEST_FILENAME).is_file() else []
            for lib in libs:
                for slug in _manifest_library_names(lib):
                    _add(slug, lib, "MAS_LIBRARY_PATHS")

        ws_root = self._workspace_root()
        if ws_root is not None:
            from mas.library_roots import iter_libraries_in_search_path
            from mas.runtime.workspace_config import library_search_paths

            ws = self._workspace
            if ws is not None and hasattr(ws, "raw_manifest_libraries"):
                # Preferred: the public accessor (real WorkspaceConfig instances).
                raw_libs = ws.raw_manifest_libraries
            else:
                # Fallback for duck-typed workspace stand-ins (e.g. tests) that
                # only carry ``_data``.
                raw_libs = (getattr(ws, "_data", {}) or {}).get("manifest_libraries")
            for rel in library_search_paths(raw_libs):
                path = Path(rel).expanduser()
                search = path if path.is_absolute() else (ws_root / rel)
                for lib in iter_libraries_in_search_path(search):
                    _add(lib.name, lib, f"manifest_libraries:{rel}")

            for rel in self._labs_search_paths():
                labs_dir = (ws_root / rel).resolve()
                if labs_dir.is_dir():
                    for path in sorted(labs_dir.glob("*.lab")):
                        _add(path.stem, path, f"labs_search_paths:{rel}")

        try:
            from mas.lab.workspace import find_workspace_root

            ws = find_workspace_root()
            if ws is not None:
                for rel in self._labs_search_paths():
                    labs_dir = ws / rel
                    if labs_dir.exists():
                        for path in sorted(labs_dir.glob("*.lab")):
                            _add(path.stem, path, f"workspace.find_workspace_root:{rel}")
        except Exception as exc:
            logger.debug("studio workspace discovery failed: %s", exc)

        cwd = Path.cwd().resolve()
        for directory in [cwd, *cwd.parents]:
            labs_dir = directory / "labs"
            if labs_dir.is_dir():
                for path in sorted(labs_dir.glob("*.lab")):
                    _add(path.stem, path, f"cwd-walk:{directory}")
            if (directory / WORKSPACE_CONFIG_FILENAME).exists():
                break

        try:
            from mas.lab.paths import labs_root

            labs = labs_root()
            if labs.exists():
                for path in sorted(labs.glob("*.lab")):
                    _add(path.stem, path, f"labs_root:{labs}")
        except Exception as exc:
            logger.debug("labs_root discovery failed: %s", exc)

        return libraries, sources

    def _log_discovery(self) -> None:
        report = self.discovery_report()
        logger.info(
            "LabRegistry: workspace_root=%s libraries=%d runtime_apps=%s",
            report.get("workspace_root"),
            len(report.get("libraries", [])),
            report.get("runtime_apps"),
        )
        for item in report.get("libraries", []):
            logger.info(
                "  library %-20s  %s  [%s]",
                item["slug"],
                item["path"],
                item["source"],
            )

    def _iter_experiment_files(self, lib_dir: Path):
        yield from self._cached_manifest_files(lib_dir, "experiment")

    def _iter_pipeline_files(self, lib_dir: Path):
        yield from self._cached_manifest_files(lib_dir, "pipeline")

    def _iter_dataset_files(self, lib_dir: Path):
        yield from self._cached_manifest_files(lib_dir, "dataset", parent_dir="datasets")

    def _iter_overlay_files(self, lib_dir: Path):
        yield from self._cached_manifest_files(lib_dir, "overlay", parent_dir="overlays")

    def _collect_mas_resources(self, lib_dir: Path) -> Dict[str, Dict[str, Any]]:
        result: Dict[str, Dict[str, Any]] = {}
        seen: set[Path] = set()

        def _add(catalog_id: str, app_path: Path) -> None:
            if not catalog_id or catalog_id in result:
                return
            resolved = app_path.resolve()
            if resolved in seen:
                return
            mas_file = None
            for name in ("mas.yaml", "mas-bench.yaml"):
                candidate = resolved / name
                if candidate.is_file():
                    mas_file = candidate
                    break
            if mas_file is None:
                from mas.apps import resolve_app_manifest

                try:
                    agent_manifest = resolve_app_manifest(resolved, app_id=resolved.name)
                except Exception:
                    return
                try:
                    rel = str(agent_manifest.relative_to(lib_dir.resolve()))
                except ValueError:
                    rel = str(agent_manifest)
                entry = {
                    "agents": {},
                    "path": rel,
                }
                if agent_manifest.is_file():
                    entry["agents"][agent_manifest.stem] = agent_manifest.read_text(
                        encoding="utf-8"
                    )
                seen.add(resolved)
                result[catalog_id] = entry
                return
            try:
                doc = load_yaml_file(mas_file)
            except Exception:
                doc = {}
            try:
                rel = str(mas_file.relative_to(lib_dir.resolve()))
            except ValueError:
                rel = str(mas_file)
            seen.add(resolved)
            result[catalog_id] = {
                "mas_yaml": mas_file.read_text(encoding="utf-8"),
                "agents": self._load_agents_from_mas_refs(resolved, doc),
                "path": rel,
            }

        # Pass 1: canonical catalog discovery (library.yaml `apps:` entries,
        # plus a scan of apps/<name>[/v*] recognised by _is_app_directory).
        # This is the primary source; passes below only add what it misses.
        try:
            from mas.library_catalog import apps_in_library

            for catalog_id, app_path in apps_in_library(lib_dir).items():
                if "/" in catalog_id:
                    continue
                _add(catalog_id, app_path)
        except Exception:
            pass

        # Pass 2: manual apps/<name> walk. Mostly redundant with pass 1, but
        # its final resolve_app_manifest() fallback also recognises a bare
        # ``<name>/<name>.yaml`` manifest — a convention _is_app_directory()
        # (and so pass 1) does not check for.
        apps_dir = lib_dir / "apps"
        if apps_dir.exists():
            for app_folder in sorted(apps_dir.iterdir()):
                if not app_folder.is_dir():
                    continue
                mas_file = app_folder / "mas.yaml"
                if mas_file.is_file():
                    _add(app_folder.name, app_folder)
                    continue
                version_dirs = [
                    d
                    for d in sorted(app_folder.iterdir())
                    if d.is_dir() and d.name.startswith("v") and (d / "mas.yaml").is_file()
                ]
                if version_dirs:
                    for ver_dir in version_dirs:
                        _add(f"{app_folder.name}@{ver_dir.name}", ver_dir)
                    continue
                from mas.apps import resolve_app_manifest

                try:
                    resolve_app_manifest(app_folder, app_id=app_folder.name)
                except Exception:
                    continue
                _add(app_folder.name, app_folder)

        # Pass 3: mas.yaml / mas-bench.yaml anywhere under lib_dir (outside
        # apps/). Required when lib_dir is itself a bare app root rather
        # than a library with an apps/ folder — e.g. an entry-point-
        # registered app (see runtime_objects("app") above) treated as a
        # "library" by collect_mas_resources(); apps_in_library() only
        # looks for library.yaml / an apps/ subfolder, so it never resolves
        # that case (see test_collect_mas_resources_from_app_root).
        for mas_name in _MAS_MANIFEST_NAMES:
            for mas_file in sorted({lib_dir / mas_name, *lib_dir.rglob(mas_name)}):
                if not mas_file.is_file():
                    continue
                if "apps" in mas_file.parts and mas_file.name == "mas.yaml":
                    continue
                try:
                    doc = load_yaml_file(mas_file)
                except Exception:
                    doc = {}
                name = (doc.get("metadata") or {}).get("name") or mas_file.stem.replace(
                    "-bench", ""
                )
                _add(str(name), mas_file.parent)

        # Pass 4: apps/<name>/<subdir>/mas.yaml where <subdir> is not a
        # ``v*`` version folder. apps_in_library()'s version scan
        # (_version_dirs) only descends into ``v*``-named children, so a
        # non-version nested layout (e.g. apps/<name>/<variant>/mas.yaml)
        # is invisible to pass 1 and needs this rglob. (A single-level
        # ``apps/*/mas.yaml`` rglob used to run here too, but every path it
        # can match is already an app directory by _is_app_directory()'s
        # own definition, so pass 1 always finds it first — confirmed with
        # apps_in_library() directly, and by this pass's own dead-hit guard
        # in _add(); it was dropped as genuinely redundant.)
        for mas_file in sorted(lib_dir.rglob("apps/*/*/mas.yaml")):
            if not mas_file.is_file():
                continue
            parent = mas_file.parent
            catalog_id = parent.name
            if parent.parent.parent.name == "apps":
                if parent.name.startswith("v"):
                    catalog_id = f"{parent.parent.name}@{parent.name}"
                else:
                    catalog_id = f"{parent.parent.name}-{parent.name}"
            _add(catalog_id, parent)
        return result

    @staticmethod
    def _mas_resource_from_app_root(app_path: Path) -> Optional[Dict[str, Any]]:
        """Build a mas_resources entry from an installed app package directory."""
        for mas_file in (
            app_path / "mas.yaml",
            app_path / "mas-bench.yaml",
        ):
            if not mas_file.is_file():
                continue
            try:
                doc = load_yaml_file(mas_file)
            except Exception:
                doc = {}
            name = (doc.get("metadata") or {}).get("name") or mas_file.stem.replace("-bench", "")
            return {
                "mas_yaml": mas_file.read_text(encoding="utf-8"),
                "agents": LabRegistry._load_agents_from_mas_refs(app_path, doc),
                "path": str(mas_file.relative_to(app_path)),
                "source": "runtime-app",
            }
        return None

    @staticmethod
    @lru_cache(maxsize=1)
    def discover_bundled_infra() -> Dict[str, str]:
        """Infra bundle YAML from installed ``mas.runtime.manifest_libraries`` packages."""
        bundled: Dict[str, str] = {}
        for ep in _entry_points(group="mas.runtime.manifest_libraries"):
            try:
                pkg_name = ep.value
                libs_path = importlib.resources.files(pkg_name) / "libs" / ep.name
                with importlib.resources.as_file(libs_path) as root:
                    if not root.exists():
                        continue
                    for yaml_file in sorted(root.rglob("*.yaml")):
                        key = f"bundles/{ep.name}/{yaml_file.relative_to(root)}"
                        bundled[key] = yaml_file.read_text(encoding="utf-8")
            except Exception:
                continue
        return bundled

    @staticmethod
    def discover_workspace_infra(ws_root: Path) -> Dict[str, str]:
        """Workspace-level infra manifests (not nested under a single *.lab)."""
        infra: Dict[str, str] = {}
        infra_dir = ws_root / "infra"
        if infra_dir.is_dir():
            for path in sorted(infra_dir.glob("*.yaml")):
                infra[f"workspace/infra/{path.name}"] = path.read_text(encoding="utf-8")
            for path in sorted(infra_dir.glob("*.yml")):
                infra[f"workspace/infra/{path.name}"] = path.read_text(encoding="utf-8")
        return infra

    @staticmethod
    def _load_agents_from_mas_refs(lib_dir: Path, mas_doc: dict) -> Dict[str, str]:
        agents: Dict[str, str] = {}
        agency = (mas_doc.get("spec") or {}).get("agency") or {}
        for agent in agency.get("agents") or []:
            if not isinstance(agent, dict):
                continue
            agent_id = agent.get("id") or agent.get("name")
            ref = agent.get("ref")
            if not ref:
                continue
            agent_path = (lib_dir / ref).resolve()
            if agent_path.exists():
                agents[str(agent_id or agent_path.stem)] = agent_path.read_text(encoding="utf-8")
        return agents

    @staticmethod
    def _parse_experiment_metadata(path: Path, lib_dir: Path | None = None) -> Dict[str, Any]:
        rel = str(path.relative_to(lib_dir)) if lib_dir else str(path)
        entry: Dict[str, Any] = {"filename": path.name, "path": rel}
        try:
            doc = load_yaml_file(path)
            exp = doc.get("experiment") or doc
            entry["name"] = exp.get("name", path.stem)
            entry["description"] = exp.get("description", "")
            entry["version"] = exp.get("version", "")
            scenarios = exp.get("scenarios", [])
            entry["scenarios"] = [s.get("id", "") for s in scenarios if isinstance(s, dict)]
            dataset = exp.get("dataset", {})
            entry["dataset"] = dataset.get("path", "") if isinstance(dataset, dict) else ""
        except Exception:
            entry.update(
                name=path.stem,
                description="",
                version="",
                scenarios=[],
                dataset="",
            )
        return entry

    @staticmethod
    def _parse_pipeline_metadata(path: Path, lib_dir: Path | None = None) -> Dict[str, Any]:
        rel = str(path.relative_to(lib_dir)) if lib_dir else str(path)
        entry: Dict[str, Any] = {"filename": path.name, "path": rel}
        try:
            doc = load_yaml_file(path)
            block = doc.get("pipeline") if isinstance(doc.get("pipeline"), dict) else doc
            meta = block.get("metadata") or doc.get("metadata") or {}
            entry["name"] = meta.get("name") or block.get("name") or path.stem
            entry["description"] = meta.get("description") or block.get("description") or ""
            entry["bind"] = doc.get("x-bind", "")
            steps = block.get("steps") or (doc.get("spec") or {}).get("steps") or []
            entry["steps"] = [
                {
                    "name": s.get("name"),
                    "type": s.get("type"),
                    "depends_on": s.get("depends_on", []),
                }
                for s in steps
                if isinstance(s, dict)
            ]
            output = block.get("output") or (doc.get("spec") or {}).get("output") or {}
            entry["experiment"] = output.get("base_dir", "")
        except Exception:
            entry.update(name=path.stem, description="", bind="", steps=[], experiment="")
        return entry

    @staticmethod
    def _dataset_description(path: Path) -> str:
        try:
            data = load_yaml_file(path)
            return str(
                data.get("description") or (data.get("metadata") or {}).get("description") or ""
            )
        except Exception:
            return ""


def get_lab_registry(workspace: Any = None, *, refresh: bool = False) -> LabRegistry:
    """Return the process-wide :class:`LabRegistry` singleton."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = LabRegistry(workspace)
    elif refresh:
        _REGISTRY.refresh()
    return _REGISTRY


def reset_lab_registry() -> None:
    """Clear singleton (tests)."""
    global _REGISTRY
    _REGISTRY = None
