#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Dataset CRUD endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from mas.lab.controller.deps import ensure_yaml_ext
from mas.lab.controller.manifest_validation import refuse_secret_values
from mas.lab.controller.models import SaveDatasetRequest
from mas.lab.controller.path_safety import UnsafeNameError, ensure_inside, is_relative_name
from mas.lab.controller.routes._api import deps

router = APIRouter()


def _dataset_path_or(datasets_dir: Path, name: str, status_code: int) -> Path:
    """``datasets_dir / name`` when every segment is a valid name and it resolves inside the directory."""
    path = datasets_dir / name
    try:
        if is_relative_name(name):
            ensure_inside(datasets_dir, path)
            return path
    except UnsafeNameError:
        pass
    raise HTTPException(status_code=status_code, detail="invalid dataset name")


@router.get("/api/libraries/{library_name}/datasets", tags=["Libraries"])
def list_datasets(library_name: str):
    """List available dataset YAML files under datasets/."""
    datasets = deps.get_manifest_store().list_datasets_meta(library_name)
    return {"datasets": datasets}


@router.post("/api/libraries/{library_name}/datasets", tags=["Libraries"], status_code=201)
def create_dataset(library_name: str, req: SaveDatasetRequest):
    """Create a new dataset file in the library's datasets/ directory."""
    lib_dir = deps.get_library_path(library_name)
    datasets_dir = lib_dir / "datasets"
    datasets_dir.mkdir(parents=True, exist_ok=True)

    filename = ensure_yaml_ext(req.name)
    file_path = _dataset_path_or(datasets_dir, filename, 400)

    if file_path.exists():
        raise HTTPException(status_code=409, detail=f"Dataset '{filename}' already exists")

    refuse_secret_values(req.content, source=filename)
    file_path.write_text(req.content, encoding="utf-8")
    deps.get_manifest_store().invalidate()
    return {"name": filename}


@router.get("/api/libraries/{library_name}/datasets/{dataset_name:path}", tags=["Libraries"])
def get_dataset(library_name: str, dataset_name: str):
    """Return the content of a specific dataset."""
    lib_dir = deps.get_library_path(library_name)
    file_path = _dataset_path_or(lib_dir / "datasets", ensure_yaml_ext(dataset_name), 404)

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_name}' not found")

    return {"name": file_path.name, "content": file_path.read_text(encoding="utf-8")}


@router.put("/api/libraries/{library_name}/datasets/{dataset_name:path}", tags=["Libraries"])
def update_dataset(library_name: str, dataset_name: str, req: SaveDatasetRequest):
    """Update an existing dataset. Supports rename via req.name."""
    lib_dir = deps.get_library_path(library_name)
    datasets_dir = lib_dir / "datasets"

    dataset_name = ensure_yaml_ext(dataset_name)
    new_name = ensure_yaml_ext(req.name)

    old_path = _dataset_path_or(datasets_dir, dataset_name, 404)
    if not old_path.is_file():
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_name}' not found")

    new_path = _dataset_path_or(datasets_dir, new_name, 400)
    if new_name != dataset_name and new_path.exists():
        raise HTTPException(status_code=409, detail=f"Dataset '{new_name}' already exists")

    refuse_secret_values(req.content, source=new_name)
    new_path.parent.mkdir(parents=True, exist_ok=True)
    if old_path != new_path:
        old_path.unlink()
    new_path.write_text(req.content, encoding="utf-8")
    deps.get_manifest_store().invalidate()
    return {"name": new_name}


@router.delete("/api/libraries/{library_name}/datasets/{dataset_name:path}", tags=["Libraries"])
def delete_dataset(library_name: str, dataset_name: str):
    """Delete a dataset file."""
    lib_dir = deps.get_library_path(library_name)
    dataset_name = ensure_yaml_ext(dataset_name)
    file_path = _dataset_path_or(lib_dir / "datasets", dataset_name, 404)

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_name}' not found")

    file_path.unlink()
    deps.get_manifest_store().invalidate()
    return {"deleted": dataset_name}
