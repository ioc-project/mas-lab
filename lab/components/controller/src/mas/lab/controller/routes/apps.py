#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""MAS app resource CRUD endpoints."""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException

from mas.ctl.overlay.canvas import apply_ui_canvas_to_yaml, persist_ui_canvas_yaml
from mas.lab.controller.lab_registry import LabRegistry
from mas.lab.controller.models import SaveMASResourceRequest
from mas.lab.controller.routes._api import deps, jobs, LIBRARIES_DIR, validate_pipeline_yaml

router = APIRouter()


def _persist_ui_canvas(app_dir: Path, mas_yaml: str, agents: dict[str, str], mas_name: str) -> tuple[str, dict[str, str]]:
    """Move canvas ``x-*`` fields from MAS/agent YAML onto a layout-only Overlay."""
    return persist_ui_canvas_yaml(app_dir, mas_yaml, agents, mas_name=mas_name)


def _write_mas_resource(apps_dir: Path, mas_name: str, mas_yaml: str, agents: dict[str, str]) -> dict:
    """Write a MAS manifest + agent files to disk. Returns summary."""
    app_dir = apps_dir / mas_name
    app_dir.mkdir(parents=True, exist_ok=True)
    mas_yaml, agents = _persist_ui_canvas(app_dir, mas_yaml, agents, mas_name)

    mas_file = app_dir / "mas.yaml"
    mas_file.write_text(mas_yaml, encoding="utf-8")

    created_agents = []
    if agents:
        agents_dir = app_dir / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        for agent_name, agent_content in agents.items():
            filename = f"{agent_name}.yaml" if not agent_name.endswith(".yaml") else agent_name
            (agents_dir / filename).write_text(agent_content, encoding="utf-8")
            created_agents.append(filename)

    return {
        "mas_name": mas_name,
        "mas_file": f"apps/{mas_name}/mas.yaml",
        "agents": sorted(created_agents),
    }


@router.post("/api/libraries/{library_name}/apps", tags=["Libraries"], status_code=201)
async def save_mas_resource(library_name: str, req: SaveMASResourceRequest):
    """Save a MAS manifest + agent files under apps/<mas_name>/."""
    lib_dir = deps.get_library_path(library_name)
    apps_dir = lib_dir / "apps"
    app_dir = apps_dir / req.mas_name

    if (app_dir / "mas.yaml").exists():
        raise HTTPException(
            status_code=409,
            detail=f"MAS resource '{req.mas_name}' already exists",
        )

    result = _write_mas_resource(apps_dir, req.mas_name, req.mas_yaml, req.agents)
    return result


@router.put("/api/libraries/{library_name}/apps/{old_mas_name}", tags=["Libraries"])
async def update_mas_resource(library_name: str, old_mas_name: str, req: SaveMASResourceRequest):
    """Update a MAS resource. Overwrites mas.yaml and agents/ only, preserving other app contents."""
    lib_dir = deps.get_library_path(library_name)
    apps_dir = lib_dir / "apps"
    old_app_dir = apps_dir / old_mas_name

    if not (old_app_dir / "mas.yaml").exists():
        raise HTTPException(
            status_code=404,
            detail=f"MAS resource '{old_mas_name}' not found",
        )

    renaming = req.mas_name != old_mas_name
    if renaming:
        new_app_dir = apps_dir / req.mas_name
        if new_app_dir.exists() and (new_app_dir / "mas.yaml").exists():
            raise HTTPException(
                status_code=409,
                detail=f"MAS resource '{req.mas_name}' already exists",
            )

    if renaming:
        target_dir = apps_dir / req.mas_name
        old_app_dir.rename(target_dir)
    else:
        target_dir = old_app_dir

    target_dir.mkdir(parents=True, exist_ok=True)
    mas_yaml, agents = _persist_ui_canvas(target_dir, req.mas_yaml, req.agents, req.mas_name)
    (target_dir / "mas.yaml").write_text(mas_yaml, encoding="utf-8")

    agents_dir = target_dir / "agents"
    if agents_dir.exists():
        shutil.rmtree(agents_dir)
    if agents:
        agents_dir.mkdir(parents=True, exist_ok=True)
        for agent_name, agent_content in agents.items():
            filename = f"{agent_name}.yaml" if not agent_name.endswith(".yaml") else agent_name
            (agents_dir / filename).write_text(agent_content, encoding="utf-8")

    return {
        "mas_name": req.mas_name,
        "mas_file": f"apps/{req.mas_name}/mas.yaml",
        "agents": sorted(
            f"{a}.yaml" if not a.endswith(".yaml") else a for a in agents
        ),
    }


@router.get("/api/libraries/{library_name}/apps", tags=["Libraries"])
async def list_mas_resources(library_name: str):
    """List all MAS resources with their manifests and agents."""
    return {"mas_resources": deps.get_manifest_store().collect_mas_resources(library_name)}


@router.get("/api/libraries/{library_name}/apps/{mas_name}", tags=["Libraries"])
async def get_mas_resource(library_name: str, mas_name: str):
    """Return a single MAS resource (manifest + agents)."""
    resources = deps.get_manifest_store().collect_mas_resources(library_name)
    entry = resources.get(mas_name)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"MAS resource '{mas_name}' not found",
        )

    mas_yaml = entry["mas_yaml"]
    agents = dict(entry.get("agents") or {})
    store = deps.get_manifest_store()
    lib_dir = deps.get_library_path(library_name)
    mas_path = entry.get("path")
    mas_dir = (lib_dir / mas_path).parent if mas_path else lib_dir / "apps" / mas_name
    registry = getattr(store, "_registry", None)
    if not isinstance(registry, LabRegistry):
        registry = LabRegistry._ephemeral()
    target_name = mas_name
    try:
        mas_doc = yaml.safe_load(mas_yaml) or {}
        if isinstance(mas_doc, dict):
            target_name = str((mas_doc.get("metadata") or {}).get("name") or mas_name).strip() or mas_name
    except Exception:
        pass
    seen: set[Path] = set()
    matched: list[tuple[Path, dict]] = []
    for name in (target_name, mas_name):
        for path, doc in registry.layout_overlays_targeting(lib_dir, kind="MAS", name=name):
            key = path.resolve()
            if key in seen:
                continue
            seen.add(key)
            matched.append((path, doc))
    mas_yaml, agents, overlay_path = apply_ui_canvas_to_yaml(
        mas_dir, mas_yaml, agents, overlays=matched
    )
    body: dict = {
        "mas_name": mas_name,
        "mas_yaml": mas_yaml,
        "agents": agents,
    }
    if overlay_path is not None:
        try:
            body["ui_canvas_overlay"] = str(overlay_path.relative_to(lib_dir))
        except ValueError:
            body["ui_canvas_overlay"] = str(overlay_path)
    return body


@router.delete("/api/libraries/{library_name}/apps/{mas_name}", tags=["Libraries"])
async def delete_mas_resource(library_name: str, mas_name: str):
    """Delete a MAS app folder (manifest + agents)."""
    lib_dir = deps.get_library_path(library_name)
    apps_dir = lib_dir / "apps"
    app_dir = apps_dir / mas_name

    if not (app_dir / "mas.yaml").exists():
        raise HTTPException(
            status_code=404,
            detail=f"MAS resource '{mas_name}' not found",
        )

    shutil.rmtree(app_dir)
    return {"deleted": mas_name}
