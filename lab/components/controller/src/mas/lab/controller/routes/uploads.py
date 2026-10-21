#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Upload files and get an opaque upload_id back."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile
from mas.lab.controller.uploads import get_upload_store

router = APIRouter()


@router.post("/api/uploads", tags=["Uploads"], status_code=201)
async def upload(file: Annotated[UploadFile, File()]):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")
    try:
        item = get_upload_store().save(file.filename or "upload", data)
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    return item.public_dict()
