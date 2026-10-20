#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""LLM-as-judge eval-output endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from mas.lab.controller.models import EvalOutputRequest
from mas.lab.controller.routes._api import deps, jobs, LIBRARIES_DIR, validate_pipeline_yaml

router = APIRouter()


@router.post("/api/libraries/{library_name}/eval-output", tags=["Libraries"], status_code=202)
async def eval_output(library_name: str, req: EvalOutputRequest):
    """Run LLM-as-judge evaluation on an events file. Returns a job_id for polling."""
    lib_dir = deps.get_library_path(library_name)

    cmd = ["mas-lab", "eval", req.events_file]
    for metric in req.metrics:
        cmd.extend(["--metric", metric])
    cmd.extend(["--model", req.model])
    job = jobs.submit_job(
        endpoint=f"/api/libraries/{library_name}/eval-output",
        cmd=cmd,
        cwd=lib_dir,
        timeout=req.timeout,
        request_body=req.model_dump(),
    )
    return {"job_id": job.id, "status": job.status.value, "command": job.command}
