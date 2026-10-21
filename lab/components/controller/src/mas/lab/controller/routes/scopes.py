#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Scope listing — libraries and labs as first-class catalogs."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from mas.lab.controller.deps import store_from_request
from mas.lab.controller.item_service import ItemError, group_items
from mas.lab.controller.kinds import LAB_KINDS, LIBRARY_BASE_KINDS
from mas.lab.controller.scope_catalog import get_scope_catalog
from mas.lab.controller.scope_service import create_lab
from pydantic import BaseModel, Field

router = APIRouter()


class CreateScopeRequest(BaseModel):
    kind: str = "lab"
    name: str
    description: Optional[str] = ""
    local_library: bool = Field(default=True)


@router.get("/api/scopes", tags=["Scopes"])
async def list_scopes(request: Request):
    catalog = get_scope_catalog(store_from_request(request))
    return {"scopes": [_public_scope(scope, counts=True) for scope in catalog.all()]}


@router.post("/api/scopes", tags=["Scopes"], status_code=201)
async def create_scope(req: CreateScopeRequest, request: Request):
    if req.kind != "lab":
        raise HTTPException(status_code=400, detail=f"cannot create scope kind {req.kind!r}")
    store = store_from_request(request)
    try:
        scope = create_lab(
            name=req.name,
            description=req.description or "",
            local_library=req.local_library,
            store=store,
        )
    except ItemError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    return _public_scope(scope, counts=True)


@router.get("/api/scopes/{scope_id}", tags=["Scopes"])
async def get_scope(scope_id: str, request: Request):
    catalog = get_scope_catalog(store_from_request(request))
    try:
        scope = catalog.get(scope_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"scope {scope_id!r} not found") from exc
    return _public_scope(scope, counts=True)


@router.get("/api/scopes/{scope_id}/config", tags=["Scopes"])
async def scope_config(scope_id: str, request: Request):
    """Infra / flavour / workspace YAML for the control panel (basename keys only)."""
    catalog = get_scope_catalog(store_from_request(request))
    try:
        scope = catalog.get(scope_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"scope {scope_id!r} not found") from exc
    return _public_config(scope)


@router.get("/api/scopes/{scope_id}/visible-libraries", tags=["Scopes"])
async def visible_libraries(scope_id: str, request: Request):
    catalog = get_scope_catalog(store_from_request(request))
    try:
        libraries = catalog.visible_libraries(scope_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"scope {scope_id!r} not found") from exc
    return {"libraries": [s.to_public() for s in libraries]}


def _public_scope(scope, *, counts: bool = False) -> dict:
    payload = scope.to_public()
    payload["kinds"] = _kinds_for(scope)
    if not counts:
        return payload
    tallies: dict[str, int] = {}
    for kind in payload["kinds"]:
        try:
            tallies[kind] = len(group_items(scope, kind))
        except KeyError:
            tallies[kind] = 0
    payload["counts"] = tallies
    return payload


def _kinds_for(scope) -> list[str]:
    if scope.kinds:
        kinds = list(scope.kinds)
    elif scope.kind == "lab":
        kinds = list(LAB_KINDS)
    else:
        kinds = list(LIBRARY_BASE_KINDS)
    if scope.kind != "library":
        return kinds
    # Advertise experiments/pipelines when the library actually has them.
    from mas.lab.controller.manifest_layout import layout_for

    for extra in ("experiment", "pipeline"):
        if extra in kinds:
            continue
        try:
            if layout_for(extra).list_located(scope):
                kinds.append(extra)
        except Exception:
            continue
    return kinds


def _public_config(scope) -> dict[str, dict[str, str]]:
    from pathlib import Path

    from mas.lab.controller.lab_registry import LabRegistry
    from mas.runtime.constants import WORKSPACE_CONFIG_FILENAME

    groups: dict[str, dict[str, str]] = {"infra": {}, "flavours": {}, "workspace": {}}
    root = scope.root
    for group, folder_name in (("infra", "infra"), ("flavours", "flavours")):
        folder = root / folder_name
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if path.is_file() and path.suffix in {".yaml", ".yml"}:
                groups[group][path.name] = path.read_text(encoding="utf-8")
    ws = root / WORKSPACE_CONFIG_FILENAME
    if ws.is_file():
        groups["workspace"][ws.name] = ws.read_text(encoding="utf-8")
    for key, content in LabRegistry.discover_bundled_infra().items():
        groups["infra"].setdefault(Path(key).name, content)
    return groups
