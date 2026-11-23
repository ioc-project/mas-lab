#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Pipeline run, validation, and CRUD endpoints."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from mas.lab.controller.manifest_validation import refuse_secret_fields, refuse_secret_values
from mas.lab.controller.models import PipelineRunRequest, SavePipelineRequest, ValidateRequest
from mas.lab.controller.path_safety import is_relative_name
from mas.lab.controller.routes._api import deps, jobs, validate_pipeline_yaml

router = APIRouter()


@router.post("/api/libraries/{library_name}/pipeline/run", tags=["Libraries"], status_code=202)
async def pipeline_run(library_name: str, req: PipelineRunRequest):
    """Run an analysis pipeline. Returns a job_id for polling."""
    lib_dir = deps.get_library_path(library_name)

    cleanup_paths: list[Path] = []

    refuse_secret_fields({"only": req.only})
    # A relative file name names a pipeline file; anything else is inline YAML content.
    if not is_relative_name(req.pipeline_yaml):
        refuse_secret_values(req.pipeline_yaml)
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", prefix="pipeline-", delete=False, dir=str(lib_dir))
        tmp.write(req.pipeline_yaml)
        tmp.close()
        pipeline_path = Path(tmp.name).name
        cleanup_paths.append(Path(tmp.name))
    else:
        pipeline_path = req.pipeline_yaml

    cmd = ["mas-lab", "benchmark", "pipeline", "run", pipeline_path]
    if req.only:
        cmd += ["--only"] + req.only
    job = jobs.submit_job(
        endpoint=f"/api/libraries/{library_name}/pipeline/run",
        cmd=cmd,
        cwd=lib_dir,
        timeout=req.timeout,
        request_body=req.model_dump(),
        cleanup_paths=cleanup_paths,
    )
    return {"job_id": job.id, "status": job.status.value, "command": job.command}


@router.post("/api/libraries/{library_name}/pipelines/validate", tags=["Libraries"])
def validate_pipeline(library_name: str, req: ValidateRequest):
    """Validate a pipeline manifest against the schema and check DAG integrity."""
    deps.get_library_path(library_name)
    result = validate_pipeline_yaml(req.manifest_yaml)
    if result["valid"]:
        return {"status": "OK Pipeline", "errors": []}
    raise HTTPException(status_code=422, detail=result["errors"])


@router.post("/api/libraries/{library_name}/pipelines", tags=["Libraries"], status_code=201)
def create_pipeline(library_name: str, req: SavePipelineRequest):
    """Create a new pipeline definition in the library's pipelines/ directory."""
    lib_dir = deps.get_library_path(library_name)
    pipe_dir = lib_dir / "pipelines"
    pipe_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{req.name}.yaml" if not req.name.endswith(".yaml") else req.name
    file_path = pipe_dir / filename

    if file_path.exists():
        raise HTTPException(status_code=409, detail=f"Pipeline '{req.name}' already exists")

    if req.run_validation:
        result = validate_pipeline_yaml(req.content)
        if not result["valid"]:
            raise HTTPException(status_code=422, detail=result["errors"])

    refuse_secret_values(req.content)
    file_path.write_text(req.content, encoding="utf-8")
    deps.get_manifest_store().invalidate()
    return {"name": req.name, "filename": filename}


@router.get("/api/libraries/{library_name}/pipelines/{pipeline_name}", tags=["Libraries"])
async def get_pipeline(library_name: str, pipeline_name: str):
    """Return the content of a specific pipeline definition."""
    lib_dir = deps.get_library_path(library_name)
    filename = f"{pipeline_name}.yaml" if not pipeline_name.endswith(".yaml") else pipeline_name
    file_path = lib_dir / "pipelines" / filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Pipeline '{pipeline_name}' not found")

    return {"name": pipeline_name, "filename": filename, "content": file_path.read_text(encoding="utf-8")}


@router.put("/api/libraries/{library_name}/pipelines/{pipeline_name}", tags=["Libraries"])
def update_pipeline(library_name: str, pipeline_name: str, req: SavePipelineRequest):
    """Update an existing pipeline definition. Supports rename via req.name."""
    lib_dir = deps.get_library_path(library_name)
    pipe_dir = lib_dir / "pipelines"

    old_filename = f"{pipeline_name}.yaml" if not pipeline_name.endswith(".yaml") else pipeline_name
    old_path = pipe_dir / old_filename

    if not old_path.exists():
        raise HTTPException(status_code=404, detail=f"Pipeline '{pipeline_name}' not found")

    new_filename = f"{req.name}.yaml" if not req.name.endswith(".yaml") else req.name
    new_path = pipe_dir / new_filename

    if req.name != pipeline_name and new_path.exists():
        raise HTTPException(status_code=409, detail=f"Pipeline '{req.name}' already exists")

    if req.run_validation:
        result = validate_pipeline_yaml(req.content)
        if not result["valid"]:
            raise HTTPException(status_code=422, detail=result["errors"])

    refuse_secret_values(req.content)
    if old_path != new_path:
        old_path.unlink()
    new_path.write_text(req.content, encoding="utf-8")
    deps.get_manifest_store().invalidate()
    return {"name": req.name, "filename": new_filename}


@router.delete("/api/libraries/{library_name}/pipelines/{pipeline_name}", tags=["Libraries"])
async def delete_pipeline(library_name: str, pipeline_name: str):
    """Delete a pipeline definition."""
    lib_dir = deps.get_library_path(library_name)
    filename = f"{pipeline_name}.yaml" if not pipeline_name.endswith(".yaml") else pipeline_name
    file_path = lib_dir / "pipelines" / filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Pipeline '{pipeline_name}' not found")

    file_path.unlink()
    deps.get_manifest_store().invalidate()
    return {"deleted": pipeline_name}
