#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Job management endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from mas.lab.controller.api import peek_controller_api
from mas.lab.controller.jobs import JobStatus, _jobs

router = APIRouter()

_TERMINAL = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.TIMEOUT}


def _origin_of(job) -> str | None:
    from mas.lab.controller.jobs import _job_origin

    return _job_origin(job.request_body)


@router.get("/api/jobs", tags=["Jobs"])
async def list_jobs(status: Optional[str] = None, origin: Optional[str] = None):
    """List FastAPI jobs plus in-process controller workers."""
    jobs = _jobs.values()
    if status:
        jobs = [j for j in jobs if j.status.value == status]
    else:
        jobs = list(jobs)
    jobs.sort(key=lambda j: j.created_at, reverse=True)
    summaries = [j.to_summary() for j in jobs]
    if origin:
        summaries = [item for item in summaries if item.get("origin") == origin]
    seen = {item["id"] for item in summaries}
    api = peek_controller_api()
    if api is not None:
        for worker in api.list_jobs(status=status):
            if worker.get("id") in seen:
                continue
            if origin and worker.get("origin") != origin:
                continue
            summaries.append(worker)
    return {"jobs": summaries}


@router.get("/api/jobs/{job_id}", tags=["Jobs"])
async def get_job(job_id: str):
    """Get full details of a specific job (including stdout/stderr)."""
    job = _jobs.get(job_id)
    if job:
        return job.to_dict()
    api = peek_controller_api()
    detail = api.get_job(job_id) if api is not None else None
    if detail:
        return detail
    from mas.lab.controller.ui_stream.app import state as ui_state

    replay = ui_state.lookup_job(job_id)
    if replay:
        return replay
    raise HTTPException(status_code=404, detail=f"Job {job_id} not found")


@router.delete("/api/jobs/{job_id}", tags=["Jobs"])
async def cancel_job(job_id: str):
    """Cancel a running job and drop it from the in-memory list."""
    job = _jobs.get(job_id)
    if job:
        if job.status in _TERMINAL:
            # Nothing to cancel -- the job already reached a terminal state
            # (completed/failed/cancelled/timeout) on its own; say so
            # distinctly rather than implying this call actually stopped
            # something, so a caller can tell the two cases apart.
            del _jobs[job_id]
            return {"message": "already terminal, removed", "job_id": job_id}
        if job._task and not job._task.done():
            job._task.cancel()
        del _jobs[job_id]
        return {"message": "removed", "job_id": job_id}
    api = peek_controller_api()
    if api is not None and api.drop_worker(job_id):
        return {"message": "removed", "job_id": job_id}
    raise HTTPException(status_code=404, detail=f"Job {job_id} not found")


@router.delete("/api/jobs", tags=["Jobs"])
async def clear_finished_jobs(origin: Optional[str] = None):
    """Remove jobs. With origin, UI-owned runs are dropped even if still running."""
    purge_running = origin is not None
    to_remove = [
        jid
        for jid, job in _jobs.items()
        if (purge_running or job.status in _TERMINAL)
        and (origin is None or _origin_of(job) == origin)
    ]
    for jid in to_remove:
        job = _jobs[jid]
        if job.status in (JobStatus.PENDING, JobStatus.RUNNING) and job._task and not job._task.done():
            job._task.cancel()
        del _jobs[jid]
    removed = len(to_remove)
    api = peek_controller_api()
    if api is not None and origin:
        removed += api.drop_workers(origin=origin, terminal_only=False)
    return {"removed": removed}
