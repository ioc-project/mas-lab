#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Library listing and resource discovery endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from mas.lab.controller.routes._api import deps, jobs, LIBRARIES_DIR, validate_pipeline_yaml
from mas.runtime.constants import LIBRARY_MANIFEST_FILENAME

router = APIRouter()


@router.get("/api/libraries", tags=["Libraries"])
async def list_libraries():
    """List available MAS libraries."""
    import yaml as _yaml

    discovered = deps.get_manifest_store().libraries()
    if discovered:
        return {"libraries": discovered}

    libraries_dir = LIBRARIES_DIR
    libraries = []
    if not libraries_dir.exists():
        return {"libraries": libraries}
    for lib_dir in sorted(libraries_dir.iterdir()):
        if not lib_dir.is_dir() or not lib_dir.name.startswith("library-"):
            continue
        lib_yaml = lib_dir / LIBRARY_MANIFEST_FILENAME
        name = lib_dir.name
        description = ""
        if lib_yaml.exists():
            with open(lib_yaml, encoding="utf-8") as f:
                data = _yaml.safe_load(f)
            if data:
                name = data.get("name", lib_dir.name)
                description = data.get("description", "").strip()
        libraries.append({
            "dir": lib_dir.name,
            "name": name,
            "description": description,
        })
    return {"libraries": libraries}


@router.get("/api/runtime-runners", tags=["Benchmark"])
async def list_runtime_runners():
    """List registered lab runtime plugins (mas, langgraph, autogen, …)."""
    from mas.lab.controller.api import ControllerAPI

    return {"runners": ControllerAPI().list_runtime_runners()}


@router.get("/api/libraries/{library_name}/tools", tags=["Libraries"])
async def list_library_tools(library_name: str, namespaces: str = "global"):
    """List available tools for a specific library.

    The *namespaces* query parameter is a comma-separated list of scopes.
    ``"global"`` returns tools under the library root ``tools/`` folder
    (prefixed with ``global/``).  Any other value is treated as an app
    name and returns tools under ``apps/{name}/tools/``.
    """
    lib_dir = deps.get_library_path(library_name)
    ns_list = [n.strip() for n in namespaces.split(",") if n.strip()]
    return {"tools": deps.discover_tools(lib_dir, ns_list)}


@router.get("/api/libraries/{library_name}/skills", tags=["Libraries"])
async def list_library_skills(library_name: str, namespaces: str = "global"):
    """List available skills for a specific library.

    ``namespaces`` is a comma-separated list of scopes: ``global`` for
    library-root skills (prefixed ``global/``), or an app name for
    app-scoped skills (no prefix).  Defaults to ``global``."""
    lib_dir = deps.get_library_path(library_name)
    ns_list = [n.strip() for n in namespaces.split(",") if n.strip()]
    return {"skills": deps.discover_skills(lib_dir, ns_list)}


@router.get("/api/libraries/{library_name}/documents", tags=["Libraries"])
async def read_library_document(library_name: str, path: str):
    """Read a file under the library root (MAS YAML, agent YAML, overlays)."""
    from fastapi import HTTPException

    rel = str(path or "").strip().lstrip("/")
    if not rel or ".." in Path(rel).parts:
        raise HTTPException(status_code=400, detail="invalid path")
    lib_dir = deps.get_library_path(library_name).resolve()
    target = (lib_dir / rel).resolve()
    if not str(target).startswith(str(lib_dir)):
        raise HTTPException(status_code=400, detail="path escapes library")
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"file not found: {rel}")
    return {"path": rel, "content": target.read_text(encoding="utf-8")}


@router.get("/api/libraries/{library_name}/topologies", tags=["Libraries"])
async def list_library_topologies(library_name: str):
    """List available topology manifests in a library."""
    lib_dir = deps.get_library_path(library_name)
    topo_dir = lib_dir / "topologies"
    if not topo_dir.exists():
        return {"topologies": []}
    files = sorted(f.name for f in topo_dir.glob("*.yaml"))
    return {"topologies": files}


@router.get("/api/libraries/{library_name}/config-files", tags=["Libraries"])
async def get_library_config_files(library_name: str):
    """Return infra, flavour, and workspace config files for the control panel."""
    return deps.get_manifest_store().config_files(library_name)


@router.get("/api/libraries/{library_name}/pipelines", tags=["Libraries"])
async def list_library_pipelines(library_name: str):
    """List available pipeline definitions with metadata."""
    pipelines = deps.get_manifest_store().list_pipelines(library_name)
    return {"pipelines": pipelines}


@router.get("/api/libraries/{library_name}/scenarios", tags=["Libraries"])
async def list_scenarios(library_name: str):
    """List available MAS scenario files (mas.yaml in apps/<name>/)."""
    lib_dir = deps.get_library_path(library_name)
    apps_dir = lib_dir / "apps"

    if not apps_dir.exists():
        return {"scenarios": []}

    scenarios = []
    for app_folder in sorted(apps_dir.iterdir()):
        if not app_folder.is_dir():
            continue
        mas_file = app_folder / "mas.yaml"
        if mas_file.exists():
            scenarios.append({
                "name": app_folder.name,
                "path": f"apps/{app_folder.name}/mas.yaml",
            })
    return {"scenarios": scenarios}
