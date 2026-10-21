#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Start, list, stream, and delete runs. Bodies carry item ids, never paths."""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from mas.lab.controller import run_bridge
from mas.lab.controller.api import peek_controller_api
from mas.lab.controller.artifacts import get_artifact_registry
from mas.lab.controller.constants import MAX_TIMEOUT
from mas.lab.controller.deps import store_from_request
from mas.lab.controller.item_service import ItemError, get_item
from mas.lab.controller.kinds import KIND_CATALOG
from mas.lab.controller.run_store import _now, get_run_store, new_run
from pydantic import BaseModel, Field

router = APIRouter()


_RUN_ACTIONS = {"run", "analyze", "import", "export"}


class StartRunRequest(BaseModel):
    scope: str
    kind: str
    item: str
    overlays: list[str] = Field(default_factory=list)
    bind: Optional[str] = None
    query: Optional[str] = None
    manifest: Optional[str] = None
    action: str = "run"
    timeout: int = Field(default=MAX_TIMEOUT, ge=1, le=MAX_TIMEOUT)


@router.post("/api/runs", tags=["Runs"], status_code=202)
async def start_run(req: StartRunRequest, request: Request):
    store = store_from_request(request)
    action = (req.action or "run").lower()
    if action not in _RUN_ACTIONS:
        raise HTTPException(status_code=400, detail=f"unknown run action {req.action!r}")
    if action == "run" and not KIND_CATALOG.get(req.kind, {}).get("runnable"):
        raise HTTPException(status_code=400, detail=f"cannot run kind {req.kind!r}")
    cwd = _cwd_for(req.scope, store)
    if action == "run" and not req.manifest:
        try:
            get_item(req.scope, req.kind, req.item, store=store)
        except ItemError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    run = new_run(
        scope=req.scope,
        kind=req.kind,
        item=req.item,
        request_summary={
            "overlays": req.overlays,
            "bind": req.bind,
            "query": req.query,
            "action": action,
            "has_inline_manifest": bool(req.manifest),
            "source": "ui",
        },
    )
    runs = get_run_store()
    run.status = "running"
    run.started_at = _now()
    if req.query:
        run.log_lines.append(req.query)
    runs.save(run)

    api = peek_controller_api()
    if api is not None and action == "run":
        path, cleanup = _item_path(req, store)
        submitted = run_bridge.submit_run(api, req, path, cwd)
        worker_id = submitted.get("worker_id") or submitted.get("job_id")
        run.job_id = worker_id
        detail = api.get_worker(worker_id) if worker_id else None
        if detail:
            run_bridge.apply_worker_to_run(run, detail, cwd=cwd)
            if run.status not in {"pending", "running"}:
                _attach_artifacts(run, cwd)
        status = (detail or submitted).get("status")
        if str(status) in run_bridge._TERMINAL:
            for leftover in cleanup:
                shutil.rmtree(leftover, ignore_errors=True)
        else:
            run_bridge.register_cleanup(run.id, cleanup)
        runs.save(run)
        return {"id": run.id, "status": run.status, "job_id": run.job_id}

    cmd, cleanup = _command_for_run(req, store, cwd=cwd)
    from mas.lab.controller import jobs as jobs_mod

    job = jobs_mod.submit_job(
        endpoint="/api/runs",
        cmd=cmd,
        cwd=cwd,
        timeout=req.timeout,
        request_body=req.model_dump(),
        cleanup_paths=cleanup or None,
    )
    run.job_id = job.id
    if job.status in jobs_mod.TERMINAL_STATUSES:
        _apply_job_to_run(run, job, cwd=cwd)
    else:
        asyncio.create_task(_follow_job(run.id, job.id, cwd))
    runs.save(run)

    return {"id": run.id, "status": run.status, "job_id": job.id}


def _item_path(req: StartRunRequest, store) -> tuple[Path, list[Path]]:
    cleanup: list[Path] = []
    if req.manifest:
        tmp_dir = Path(tempfile.mkdtemp(prefix="mas-run-"))
        path = tmp_dir / f"{req.kind}.yaml"
        path.write_text(req.manifest, encoding="utf-8")
        cleanup.append(tmp_dir)
        return path, cleanup
    from mas.lab.controller.manifest_layout import layout_for
    from mas.lab.controller.scope_catalog import get_scope_catalog

    try:
        scope = get_scope_catalog(store).get(req.scope)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"scope {req.scope!r} not found") from exc
    located = layout_for(req.kind).locate(scope, req.item)
    if located is None or not located.manifest_path.is_file():
        raise HTTPException(status_code=404, detail=f"{req.kind} {req.item!r} not found")
    return located.manifest_path, cleanup


def _command_for_run(req: StartRunRequest, store, *, cwd: Path) -> tuple[list[str], list[Path]]:
    """Build the mas-ctl / mas-lab argv for a started run."""
    action = (req.action or "run").lower()
    if action in {"analyze", "export"} and ("/" in req.item or "\\" in req.item or ".." in req.item):
        raise HTTPException(status_code=400, detail="item must be an id, not a path")
    if action == "analyze":
        return ["mas-lab", "benchmark", "analyze", req.item], []
    if action == "import":
        from mas.lab.controller.uploads import get_upload_store

        uploaded = get_upload_store().get(req.item)
        if uploaded is None:
            raise HTTPException(status_code=404, detail=f"upload {req.item!r} not found")
        return ["mas-lab", "benchmark", "import", str(uploaded.path)], []
    if action == "export":
        out_dir = cwd / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{Path(req.item).name}.tar.gz"
        return ["mas-lab", "benchmark", "export", req.item, "--output", str(out_path)], []
    path, cleanup = _item_path(req, store)
    if req.kind == "experiment":
        return ["mas-lab", "benchmark", "run", str(path)], cleanup
    if req.kind == "pipeline":
        return ["mas-lab", "benchmark", "pipeline", "run", str(path)], cleanup
    cmd = ["mas-ctl", "run-mas", str(path)]
    if req.query:
        cmd.extend(["-q", req.query])
    for overlay in req.overlays:
        cmd.extend(["--overlay", overlay])
    return cmd, cleanup


def _apply_job_to_run(run, job, *, cwd: Path | None = None) -> None:
    status = job.status.value if hasattr(job.status, "value") else str(job.status)
    run.status = {
        "pending": "pending",
        "running": "running",
        "completed": "completed",
        "failed": "failed",
        "cancelled": "failed",
        "timeout": "failed",
        "interrupted": "interrupted",
    }.get(status, status)
    run.exit_code = job.exit_code
    if job.started_at:
        run.started_at = job.started_at
    run.finished_at = job.finished_at
    seen = set(run.log_lines)
    for chunk in (job.stdout, job.stderr, job.error):
        text = (chunk or "").rstrip()
        if text and text not in seen:
            run.log_lines.append(text)
            seen.add(text)
    if run.status not in {"pending", "running"}:
        _attach_artifacts(run, cwd)


def _attach_artifacts(run, cwd: Path | None) -> None:
    registry = get_artifact_registry()
    if registry.for_run(run.id):
        return
    root = None
    if run.result_dir:
        candidate = Path(run.result_dir)
        if candidate.is_dir():
            root = candidate
    if root is None and cwd is not None:
        for name in ("output", "results"):
            candidate = cwd / name
            if candidate.is_dir():
                root = candidate
                break
    if root is None:
        return
    registry.register_tree(run.id, root)
    run.result_dir = str(root)


async def _follow_job(run_id: str, job_id: str, cwd: Path) -> None:
    from mas.lab.controller import jobs as jobs_mod

    store = get_run_store()
    while True:
        job = jobs_mod.get_job(job_id)
        run = store.get(run_id)
        if run is None or job is None:
            return
        _apply_job_to_run(run, job, cwd=cwd)
        store.save(run)
        if job.status in jobs_mod.TERMINAL_STATUSES:
            return
        await asyncio.sleep(0.2)


def _lookup_run(run_id: str):
    store = get_run_store()
    return store.get(run_id) or next((row for row in store.list() if row.job_id == run_id), None)


def _cwd_for(scope_id: str, store) -> Path:
    from mas.lab.controller.scope_catalog import get_scope_catalog

    try:
        return get_scope_catalog(store).get(scope_id).root
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"scope {scope_id!r} not found") from exc


def _adopt_labs_dir_runs(store) -> None:
    """Expose ``paths.labs_dir`` / XDG experiment trees as completed runs.

    ``mas-lab benchmark import`` and ``task fetch-results`` land here, not in
    the controller run store. The experiment page lists runs by ``item``
    name, so a matching folder becomes a result tree the UI can open.
    """
    try:
        from mas.lab.paths import labs_root

        root = labs_root()
    except Exception:
        return
    if not root.is_dir():
        return
    from mas.lab.controller.manifest_layout import layout_for
    from mas.lab.controller.scope_catalog import get_scope_catalog

    try:
        layout = layout_for("experiment")
        catalog = get_scope_catalog(store)
    except Exception:
        return
    runs = get_run_store()
    known: set[tuple[str, str, str]] = set()
    for row in runs.list():
        if not row.result_dir:
            continue
        try:
            known.add((row.scope, row.item, str(Path(row.result_dir).resolve())))
        except OSError:
            continue
    for scope in catalog.all():
        try:
            located = layout.list_located(scope)
        except Exception:
            continue
        for item in located:
            out = root / item.item_id
            if not out.is_dir():
                continue
            try:
                resolved = str(out.resolve())
            except OSError:
                continue
            if (scope.id, item.item_id, resolved) in known:
                continue
            run = new_run(
                scope=scope.id,
                kind="experiment",
                item=item.item_id,
                request_summary={"source": "labs_dir", "action": "import"},
            )
            run.id = f"labsdir-{scope.id.replace(':', '-')}-{item.item_id}"
            run.status = "completed"
            run.result_dir = resolved
            runs.save(run)
            _attach_artifacts(run, out)
            runs.save(run)
            known.add((scope.id, item.item_id, resolved))


def _sync_runs(store) -> None:
    run_bridge.sync_workers(store)
    _adopt_labs_dir_runs(store)


@router.get("/api/runs", tags=["Runs"])
async def list_runs(request: Request):
    _sync_runs(store_from_request(request))
    return {"runs": [r.to_public() for r in get_run_store().list()]}


@router.get("/api/runs/{run_id}", tags=["Runs"])
async def get_run(run_id: str, request: Request):
    _sync_runs(store_from_request(request))
    run = _lookup_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    run_bridge.refresh_run(run)
    get_run_store().save(run)
    return run.to_public()


@router.get("/api/runs/{run_id}/events", tags=["Runs"])
async def run_events(run_id: str, request: Request):
    _sync_runs(store_from_request(request))
    run = _lookup_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    tracked_id = run.id

    async def gen():
        last = 0
        while True:
            current = get_run_store().get(tracked_id)
            if current is None:
                break
            run_bridge.refresh_run(current)
            get_run_store().save(current)
            lines = current.log_lines[last:]
            for line in lines:
                yield f"event: log\ndata: {json.dumps({'line': line})}\n\n"
            last += len(lines)
            yield f"event: status\ndata: {json.dumps({'status': current.status})}\n\n"
            if current.status not in {"pending", "running"}:
                yield f"event: done\ndata: {json.dumps({'status': current.status})}\n\n"
                break
            import asyncio

            await asyncio.sleep(0.2)

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/api/runs/{run_id}/artifacts", tags=["Runs"])
async def run_artifacts(run_id: str, request: Request):
    _sync_runs(store_from_request(request))
    run = _lookup_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    arts = get_artifact_registry().for_run(run.id)
    if not arts:
        cwd = None
        try:
            cwd = _cwd_for(run.scope, None)
        except HTTPException:
            cwd = Path(run.result_dir) if run.result_dir else None
        _attach_artifacts(run, cwd)
        get_run_store().save(run)
        arts = get_artifact_registry().for_run(run.id)
    return {"artifacts": [a.public_dict() for a in arts]}


@router.delete("/api/runs/{run_id}", tags=["Runs"])
async def delete_run(run_id: str):
    store = get_run_store()
    run = _lookup_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    if run.job_id:
        run_bridge.forget_worker(run.job_id)
    get_artifact_registry().drop_run(run.id)
    store.delete(run.id)
    return {"ok": True}
