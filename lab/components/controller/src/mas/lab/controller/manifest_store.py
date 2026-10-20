#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Library-scoped YAML manifest storage for the UI."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

from mas.runtime.constants import WORKSPACE_CONFIG_FILENAME
from mas.lab.controller.lab_registry import LabRegistry, _library_description
from mas.lab.controller.lab_registry import get_lab_registry
from mas.ctl.overlay.canvas import is_ui_canvas_overlay
from mas.runtime.spec.source import load_yaml_file

logger = logging.getLogger(__name__)

_SUBDIRS = {
    "experiments": "experiments",
    "pipelines": "pipelines",
    "overlays": "overlays",
    "datasets": "datasets",
    "apps": "apps",
    "scenarios": "scenarios",
}


class ManifestStore:
    """Read/write YAML resources under library roots."""

    def __init__(self, workspace: Any = None, *, registry: LabRegistry | None = None) -> None:
        """Create a store, optionally injecting a specific ``registry``.

        By default the store uses the process-wide :func:`get_lab_registry`
        singleton. Pass ``registry`` (e.g. a ``LabRegistry`` built with
        ``MAS_LAB_REGISTRY_EAGER_REFRESH=0`` and manually populated
        ``_libraries``) to use an isolated registry instead — this is how
        tests avoid both the global singleton and expensive discovery,
        without bypassing ``__init__`` via ``__new__``.
        """
        self._workspace = workspace
        self._registry = registry if registry is not None else get_lab_registry(workspace)
        self._libraries: Dict[str, Path] = {}
        self._refreshed = False

    def _ensure_ready(self) -> None:
        # Many tests override _libraries directly; avoid expensive discovery if already injected.
        if self._libraries:
            return
        if not self._refreshed:
            self.refresh()

    def refresh(self) -> None:
        self._registry.refresh()
        self._libraries = self._registry.library_paths()
        self._refreshed = True

    def invalidate(self) -> None:
        """Drop cached manifest-file listings (see LabRegistry.invalidate_manifest_cache)."""
        self._registry.invalidate_manifest_cache()

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
        return self._registry.library_root(library)

    def list_experiments(self, library: str) -> List[Dict[str, Any]]:
        root = self.library_root(library)
        return [
            LabRegistry._parse_experiment_metadata(p, root)
            for p in self._registry._iter_experiment_files(root)
        ]

    def list_pipelines(self, library: str) -> List[Dict[str, Any]]:
        root = self.library_root(library)
        return [
            LabRegistry._parse_pipeline_metadata(p, root)
            for p in self._registry._iter_pipeline_files(root)
        ]

    def list_datasets_meta(self, library: str) -> List[Dict[str, str]]:
        root = self.library_root(library)
        return [
            {
                "name": path.name,
                "path": str(path.relative_to(root)),
                "description": LabRegistry._dataset_description(path),
            }
            for path in self._registry._iter_dataset_files(root)
        ]

    def list_overlays(self, library: str) -> List[Dict[str, Any]]:
        root = self.library_root(library)
        overlays: List[Dict[str, Any]] = []
        for path in self._registry._iter_overlay_files(root):
            rel = str(path.relative_to(root))
            entry: Dict[str, Any] = {
                "name": path.stem,
                "filename": path.name,
                "path": rel,
                "ui_canvas": False,
            }
            try:
                data = load_yaml_file(path)
                meta = data.get("metadata", {})
                entry["description"] = str(meta.get("description", "") or "")
                entry["namespace"] = str((data.get("metadata") or {}).get("namespace", "global") or "global")
                entry["ui_canvas"] = is_ui_canvas_overlay(data, path=path)
            except Exception:
                entry["description"] = ""
                entry["namespace"] = "global"
            overlays.append(entry)
        return overlays

    def collect_mas_resources(self, library: str) -> Dict[str, Dict[str, Any]]:
        root = self.library_root(library)
        return self._registry._collect_mas_resources(root)

    def list_yaml_resources(self, library: str, resource: str) -> List[Dict[str, Any]]:
        sub = _SUBDIRS.get(resource, resource)
        root = self.library_root(library) / sub
        if not root.exists():
            return []
        items: List[Dict[str, Any]] = []
        for path in sorted(root.rglob("*.yaml")):
            rel = path.relative_to(root)
            name = path.stem if rel.parent == Path(".") else str(rel.with_suffix(""))
            items.append({"name": name, "path": str(rel), "file": str(path)})
        return items

    def read_text(self, library: str, resource: str, name: str) -> str:
        path = self._resolve_resource_path(library, resource, name)
        return path.read_text(encoding="utf-8")

    def write_text(self, library: str, resource: str, name: str, content: str) -> Path:
        path = self._resolve_resource_path(library, resource, name, create=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.rstrip() + "\n", encoding="utf-8")
        return path

    def delete_resource(self, library: str, resource: str, name: str) -> None:
        path = self._resolve_resource_path(library, resource, name)
        if path.exists():
            path.unlink()

    def config_files(self, library: str) -> Dict[str, Dict[str, str]]:
        """Infra, flavours, and workspace YAML for the control panel."""
        root = self.library_root(library)
        flavours: Dict[str, str] = {}
        infra: Dict[str, str] = {}

        for path in sorted(p for p in root.rglob("flavours/*") if p.suffix in (".yaml", ".yml")):
            if path.is_file():
                flavours[str(path.relative_to(root))] = path.read_text(encoding="utf-8")

        for path in sorted(p for p in root.rglob("infra/*") if p.suffix in (".yaml", ".yml")):
            if path.is_file():
                infra[str(path.relative_to(root))] = path.read_text(encoding="utf-8")

        ws_root = self._registry._workspace_root()
        if ws_root is not None:
            infra.update(LabRegistry.discover_workspace_infra(ws_root))
        infra.update(LabRegistry.discover_bundled_infra())

        workspace: Dict[str, str] = {}
        if ws_root is not None:
            ws_file = ws_root / WORKSPACE_CONFIG_FILENAME
            if ws_file.is_file():
                workspace[WORKSPACE_CONFIG_FILENAME] = ws_file.read_text(encoding="utf-8")
        if not workspace:
            local_ws = root / WORKSPACE_CONFIG_FILENAME
            if local_ws.is_file():
                workspace[WORKSPACE_CONFIG_FILENAME] = local_ws.read_text(encoding="utf-8")

        return {
            "infra": infra,
            "flavours": flavours,
            "workspace": workspace,
        }

    def _resolve_resource_path(
        self,
        library: str,
        resource: str,
        name: str,
        *,
        create: bool = False,
    ) -> Path:
        sub = _SUBDIRS.get(resource, resource)
        root = self.library_root(library) / sub
        candidate = (root / name).with_suffix(".yaml")
        if candidate.exists() or create:
            return candidate
        for path in root.rglob("*.yaml"):
            if path.stem == name or str(path.relative_to(root).with_suffix("")) == name:
                return path
        if create:
            return candidate
        raise FileNotFoundError(f"{resource}/{name} not found in library {library!r}")
