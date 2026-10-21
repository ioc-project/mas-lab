#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Item catalog: list/get/create/update/fork/rename/delete/check."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from mas.lab.controller import item_service
from mas.lab.controller.deps import store_from_request
from mas.lab.controller.kinds import list_kinds
from mas.lab.controller.scope_catalog import get_scope_catalog
from pydantic import BaseModel, Field

router = APIRouter()


class CreateItemRequest(BaseModel):
    kind: str
    name: str
    version: Optional[str] = None
    template: Optional[str] = None
    parent: Optional[str] = None
    upload_id: Optional[str] = None


class UpdateItemRequest(BaseModel):
    manifest: str
    children: dict[str, str] = Field(default_factory=dict)
    revision: str


class ForkItemRequest(BaseModel):
    version: Optional[str] = None
    name: Optional[str] = None
    target_scope: Optional[str] = None


class RenameItemRequest(BaseModel):
    name: str


class CheckItemRequest(BaseModel):
    kind: str
    name: str
    version: Optional[str] = None


class ValidateScopeRequest(BaseModel):
    kind: Optional[str] = None
    manifest: str


def _raise(exc: item_service.ItemError) -> None:
    raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


@router.get("/api/kinds", tags=["Kinds"])
async def get_kinds():
    return {"kinds": list_kinds()}


@router.get("/api/scopes/{scope_id}/items", tags=["Items"])
async def list_items(
    scope_id: str,
    request: Request,
    kind: str = Query(...),
    q: Optional[str] = None,
):
    store = store_from_request(request)
    try:
        scope = get_scope_catalog(store).get(scope_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"scope {scope_id!r} not found") from exc
    try:
        groups = item_service.group_items(scope, kind, q=q)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"unknown kind {kind!r}") from exc
    return {"items": groups}


@router.get("/api/scopes/{scope_id}/items/{kind}/{item_id}", tags=["Items"])
async def get_item(scope_id: str, kind: str, item_id: str, request: Request):
    try:
        return item_service.get_item(scope_id, kind, item_id, store=store_from_request(request))
    except item_service.ItemError as exc:
        _raise(exc)


@router.post("/api/scopes/{scope_id}/items", tags=["Items"], status_code=201)
async def create_item(scope_id: str, req: CreateItemRequest, request: Request):
    try:
        return item_service.create_item(
            scope_id,
            kind=req.kind,
            name=req.name,
            version=req.version,
            template=req.template,
            parent=req.parent,
            upload_id=req.upload_id,
            store=store_from_request(request),
        )
    except item_service.ItemError as exc:
        _raise(exc)


@router.put("/api/scopes/{scope_id}/items/{kind}/{item_id}", tags=["Items"])
async def update_item(scope_id: str, kind: str, item_id: str, req: UpdateItemRequest, request: Request):
    try:
        return item_service.update_item(
            scope_id,
            kind,
            item_id,
            manifest=req.manifest,
            children=req.children,
            revision=req.revision,
            store=store_from_request(request),
        )
    except item_service.ItemError as exc:
        _raise(exc)


@router.post("/api/scopes/{scope_id}/items/{kind}/{item_id}/fork", tags=["Items"], status_code=201)
async def fork_item(scope_id: str, kind: str, item_id: str, req: ForkItemRequest, request: Request):
    try:
        return item_service.fork_item(
            scope_id,
            kind,
            item_id,
            version=req.version,
            name=req.name,
            target_scope=req.target_scope,
            store=store_from_request(request),
        )
    except item_service.ItemError as exc:
        _raise(exc)


@router.post("/api/scopes/{scope_id}/items/{kind}/{item_id}/rename", tags=["Items"])
async def rename_item(scope_id: str, kind: str, item_id: str, req: RenameItemRequest, request: Request):
    try:
        return item_service.rename_item(scope_id, kind, item_id, name=req.name, store=store_from_request(request))
    except item_service.ItemError as exc:
        _raise(exc)


@router.delete("/api/scopes/{scope_id}/items/{kind}/{item_id}", tags=["Items"])
async def delete_item(scope_id: str, kind: str, item_id: str, request: Request):
    try:
        item_service.delete_item(scope_id, kind, item_id, store=store_from_request(request))
    except item_service.ItemError as exc:
        _raise(exc)
    return {"ok": True}


@router.post("/api/scopes/{scope_id}/items/check", tags=["Items"])
async def check_item(scope_id: str, req: CheckItemRequest, request: Request):
    return item_service.check_name(
        scope_id, req.kind, req.name, req.version, store=store_from_request(request)
    )


@router.get("/api/resolve", tags=["Items"])
async def resolve(ref: str, request: Request):
    try:
        return item_service.resolve_ref(ref, store=store_from_request(request))
    except item_service.ItemError as exc:
        _raise(exc)


@router.post("/api/scopes/{scope_id}/validate", tags=["Items"])
async def validate_in_scope(scope_id: str, req: ValidateScopeRequest, request: Request):
    from mas.lab.controller.manifest_validation import validate_manifest_yaml_content
    from mas.lab.controller.scope_catalog import get_scope_catalog as catalog_of

    try:
        scope = catalog_of(store_from_request(request)).get(scope_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"scope {scope_id!r} not found") from exc
    result = validate_manifest_yaml_content(
        req.manifest,
        base_dir=scope.root,
        kind=req.kind,
        resolve_refs=True,
    )
    return result
