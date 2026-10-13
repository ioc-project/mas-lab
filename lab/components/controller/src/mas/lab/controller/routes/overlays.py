#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Overlay CRUD and validation endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from mas.lab.controller.models import SaveOverlayRequest, ValidateRequest
from mas.lab.controller.routes._api import deps, jobs, LIBRARIES_DIR, validate_pipeline_yaml

router = APIRouter()


def _overlay_dir_for_namespace(lib_dir: Path, namespace: str | None) -> Path:
    """Return the overlays directory for the given namespace.

    ``"global"`` or ``None`` → ``{lib_dir}/overlays/``
    anything else            → ``{lib_dir}/apps/{namespace}/overlays/``
    """
    if not namespace or namespace == "global":
        return lib_dir / "overlays"
    return lib_dir / "apps" / namespace / "overlays"


def _extract_namespace_from_yaml(content: str) -> str:
    """Extract the namespace from overlay YAML content.

    Checks root-level ``x-namespace`` first (UI-generated overlays), then
    falls back to ``metadata.namespace``, defaulting to ``"global"``.
    """
    import yaml as _yaml
    try:
        doc = _yaml.safe_load(content) or {}
        return str(
            doc.get("x-namespace")
            or (doc.get("metadata") or {}).get("namespace")
            or "global"
        )
    except Exception:
        return "global"


def _find_overlay_file(lib_dir: Path, overlay_name: str) -> Path | None:
    """Search for an overlay file across global, app-scoped, and nested app dirs."""
    filename = f"{overlay_name}.yaml" if not overlay_name.endswith(".yaml") else overlay_name
    global_path = lib_dir / "overlays" / filename
    if global_path.exists():
        return global_path
    apps_dir = lib_dir / "apps"
    if apps_dir.exists():
        for pattern in (f"*/overlays/{filename}", f"*/*/overlays/{filename}"):
            matches = sorted(apps_dir.glob(pattern))
            if matches:
                return matches[0]
    return None


@router.post("/api/libraries/{library_name}/overlays/validate", tags=["Libraries"])
async def validate_overlay(library_name: str, req: ValidateRequest):
    """Validate a kind: Overlay overlay manifest.

    Uses the same schema (overlay.schema.yaml) regardless of whether the overlay
    is intended for mas-ctl run-mas or mas-lab benchmark run — the schema does
    not differentiate between the two usage contexts.
    """
    lib_dir = deps.get_library_path(library_name)
    errors = await deps.validate_overlay_content(req.manifest_yaml, lib_dir)
    if errors is None:
        return {"status": "OK", "errors": []}
    raise HTTPException(status_code=422, detail=errors)


@router.get("/api/libraries/{library_name}/overlays", tags=["Libraries"])
async def list_library_overlays(library_name: str):
    """List available overlay files with name and description (includes nested lab modules)."""
    return {"overlays": deps.get_manifest_store().list_overlays(library_name)}


@router.post("/api/libraries/{library_name}/overlays", tags=["Libraries"], status_code=201)
async def create_overlay(library_name: str, req: SaveOverlayRequest):
    """Create a new overlay definition.

    Storage location is determined by ``metadata.namespace`` in the YAML content:
    ``global`` → ``{lib}/overlays/``, otherwise → ``{lib}/apps/{ns}/overlays/``.
    """
    lib_dir = deps.get_library_path(library_name)
    namespace = _extract_namespace_from_yaml(req.content)
    overlay_dir = _overlay_dir_for_namespace(lib_dir, namespace)
    overlay_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{req.name}.yaml" if not req.name.endswith(".yaml") else req.name
    file_path = overlay_dir / filename

    if file_path.exists():
        raise HTTPException(status_code=409, detail=f"Overlay '{req.name}' already exists")

    if req.run_validation:
        result = await deps.validate_overlay_content(req.content, lib_dir)
        if result is not None:
            raise HTTPException(status_code=422, detail=result)

    file_path.write_text(req.content, encoding="utf-8")
    deps.get_manifest_store().invalidate()
    return {"name": req.name, "filename": filename}


@router.get("/api/libraries/{library_name}/overlays/{overlay_name}", tags=["Libraries"])
async def get_overlay(library_name: str, overlay_name: str):
    """Return the content of a specific overlay definition."""
    lib_dir = deps.get_library_path(library_name)
    file_path = _find_overlay_file(lib_dir, overlay_name)

    if file_path is None:
        raise HTTPException(status_code=404, detail=f"Overlay '{overlay_name}' not found")

    return {"name": overlay_name, "filename": file_path.name, "content": file_path.read_text(encoding="utf-8")}


@router.put("/api/libraries/{library_name}/overlays/{overlay_name}", tags=["Libraries"])
async def update_overlay(library_name: str, overlay_name: str, req: SaveOverlayRequest):
    """Update an existing overlay definition. Supports rename and namespace change."""
    lib_dir = deps.get_library_path(library_name)
    old_path = _find_overlay_file(lib_dir, overlay_name)

    if old_path is None:
        raise HTTPException(status_code=404, detail=f"Overlay '{overlay_name}' not found")

    namespace = _extract_namespace_from_yaml(req.content)
    new_overlay_dir = _overlay_dir_for_namespace(lib_dir, namespace)
    new_overlay_dir.mkdir(parents=True, exist_ok=True)

    new_filename = f"{req.name}.yaml" if not req.name.endswith(".yaml") else req.name
    new_path = new_overlay_dir / new_filename

    if new_path != old_path and new_path.exists():
        raise HTTPException(status_code=409, detail=f"Overlay '{req.name}' already exists")

    if req.run_validation:
        result = await deps.validate_overlay_content(req.content, lib_dir)
        if result is not None:
            raise HTTPException(status_code=422, detail=result)

    if old_path != new_path:
        old_path.unlink()
    new_path.write_text(req.content, encoding="utf-8")
    deps.get_manifest_store().invalidate()
    return {"name": req.name, "filename": new_filename}


@router.delete("/api/libraries/{library_name}/overlays/{overlay_name}", tags=["Libraries"])
async def delete_overlay(library_name: str, overlay_name: str):
    """Delete an overlay definition."""
    lib_dir = deps.get_library_path(library_name)
    file_path = _find_overlay_file(lib_dir, overlay_name)

    if file_path is None:
        raise HTTPException(status_code=404, detail=f"Overlay '{overlay_name}' not found")

    file_path.unlink()
    deps.get_manifest_store().invalidate()
    return {"deleted": overlay_name}
