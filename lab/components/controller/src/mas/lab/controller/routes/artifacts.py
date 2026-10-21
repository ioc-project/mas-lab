#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Serve run artifacts by issued id only."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from mas.lab.controller.artifacts import get_artifact_registry

router = APIRouter()

_TEXT_TYPES = {
    "text/plain",
    "text/csv",
    "text/yaml",
    "text/html",
    "text/xml",
    "application/json",
    "application/yaml",
    "image/svg+xml",
}


@router.get("/api/artifacts/{artifact_id}", tags=["Artifacts"])
async def get_artifact(artifact_id: str, download: bool = False):
    art = get_artifact_registry().get(artifact_id)
    if art is None or art.path is None or not art.path.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    if download or art.media_type not in _TEXT_TYPES and not art.media_type.startswith("text/"):
        return FileResponse(
            path=str(art.path),
            media_type=art.media_type,
            filename=art.name,
        )
    text = art.path.read_text(encoding="utf-8", errors="replace")
    return PlainTextResponse(text, media_type=art.media_type or "text/plain")
