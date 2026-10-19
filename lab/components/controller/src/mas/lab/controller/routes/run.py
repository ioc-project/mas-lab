#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Agent and MAS run endpoints."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException

from mas.lab.controller.constants import WEB_SEARCH_CACHE_DIR
from mas.lab.controller.models import MultiTurnRequest, RunAgentRequest, RunMASRequest
from mas.lab.controller.routes._api import deps, jobs, LIBRARIES_DIR, validate_pipeline_yaml

router = APIRouter()


def _resolve_overlay_arg(lib_dir: Path, ov: str) -> str:
    raw = Path(ov)
    if raw.is_file():
        try:
            return str(raw.resolve().relative_to(lib_dir.resolve()))
        except ValueError:
            return str(raw)
    candidate = lib_dir / ov
    if candidate.is_file():
        return str(candidate.relative_to(lib_dir))
    stem = raw.stem
    hits = [
        path
        for path in lib_dir.rglob(f"{stem}.yaml")
        if "overlays" in path.parts and "versions" not in path.parts
    ]
    hits.sort(key=lambda path: (0 if path.parent.name == "overlays" else 1, str(path)))
    if hits:
        return str(hits[0].relative_to(lib_dir))
    return ov


def _infra_args(lib_dir: Path, infra_refs: list[str]) -> list[str]:
    args: list[str] = []
    if infra_refs:
        for item in infra_refs:
            path = Path(item)
            resolved = path if path.is_absolute() else (lib_dir / item)
            args += ["--infra-ref", str(resolved)]
        return args
    seen: set[Path] = set()
    for infra_dir in [lib_dir / "infra", lib_dir.parent / "infra"]:
        if infra_dir.is_dir():
            for infra_file in sorted(infra_dir.glob("*.yaml")):
                resolved = infra_file.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    args += ["--infra-ref", str(infra_file)]
    return args


@router.post("/api/libraries/{library_name}/run", tags=["Libraries"], status_code=202)
async def run_agent(library_name: str, req: RunAgentRequest):
    """Run an agent with a single query in-process. Returns a job_id for polling."""
    lib_dir = deps.get_library_path(library_name)

    if req.no_cache and WEB_SEARCH_CACHE_DIR.exists():
        shutil.rmtree(WEB_SEARCH_CACHE_DIR, ignore_errors=True)

    job = jobs.submit_agent_chat_job(
        endpoint=f"/api/libraries/{library_name}/run",
        manifest_yaml=req.manifest_yaml,
        query=req.query,
        lib_dir=lib_dir,
        flavour=req.flavour,
        session_id=req.session_id,
        timeout=req.timeout,
        request_body={"query": req.query, "flavour": req.flavour},
    )
    return {
        "job_id": job.id,
        "status": job.status.value,
        "session_id": job.session_id,
        "command": job.command,
    }


@router.post("/api/libraries/{library_name}/run/multi-turn", tags=["Libraries"], status_code=202)
async def run_agent_multi_turn(library_name: str, req: MultiTurnRequest):
    """Run an agent with multiple sequential queries. Returns a job_id for polling."""
    lib_dir = deps.get_library_path(library_name)

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", prefix="agent-mt-", delete=False, dir=str(lib_dir)
    )
    tmp.write(req.manifest_yaml)
    tmp.close()
    tmp_path = Path(tmp.name)

    cmd = ["mas-runtime", "run-agent", tmp_path.name]
    if req.verbose:
        cmd.append("-v")
    for ov in req.overlays:
        cmd += ["--overlay", ov]
    if req.flavour:
        cmd += ["--flavour", req.flavour]
    for q in req.queries:
        cmd += ["-q", q]
    job = jobs.submit_job(
        endpoint=f"/api/libraries/{library_name}/run/multi-turn",
        cmd=cmd,
        cwd=lib_dir,
        timeout=req.timeout,
        request_body=req.model_dump(),
        cleanup_paths=[tmp_path],
    )
    return {"job_id": job.id, "status": job.status.value, "command": job.command}


@router.post("/api/libraries/{library_name}/run-mas", tags=["Libraries"], status_code=202)
async def run_mas(library_name: str, req: RunMASRequest):
    """Run a MAS with a single query. Returns a job_id for polling."""
    import yaml

    lib_dir = deps.get_library_path(library_name)
    cleanup_paths: list[Path] = []
    mas_rel: str

    if req.mas_path:
        mas_file = lib_dir / req.mas_path
        if not mas_file.is_file():
            raise HTTPException(status_code=404, detail=f"MAS not found: {req.mas_path}")
        mas_rel = req.mas_path
    elif req.manifest_yaml.strip():
        manifest_dir = lib_dir
        try:
            manifest_data = yaml.safe_load(req.manifest_yaml)
            mas_name = (manifest_data or {}).get("metadata", {}).get("name", "")
            if mas_name:
                app_dir = lib_dir / "apps" / mas_name
                if app_dir.is_dir():
                    manifest_dir = app_dir
        except Exception:
            pass
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", prefix="mas-run-", delete=False, dir=str(manifest_dir)
        )
        tmp.write(req.manifest_yaml)
        tmp.close()
        tmp_path = Path(tmp.name)
        cleanup_paths.append(tmp_path)
        mas_rel = str(tmp_path.relative_to(lib_dir))
    else:
        raise HTTPException(status_code=400, detail="needs: mas_path or manifest_yaml")

    cmd = ["mas-ctl"]
    if req.verbose:
        cmd.append("-v")
    cmd += ["run-mas", mas_rel]
    for ov in req.overlays:
        cmd += ["--overlay", _resolve_overlay_arg(lib_dir, ov)]
    if req.flavour:
        cmd += ["--flavour", req.flavour]
    cmd += _infra_args(lib_dir, req.infra_refs)
    cmd += ["-q", req.query]
    env_override: dict[str, str] = {}
    if req.demo_ui_ingest_url:
        env_override["MAS_DEMO_UI_INGEST_URL"] = req.demo_ui_ingest_url
    if req.demo_ui_ref:
        env_override["MAS_DEMO_UI_REF"] = json.dumps(req.demo_ui_ref)
    events_file = Path(tempfile.mkdtemp(prefix="mas-demo-ui-")) / "traces" / "events.jsonl"
    events_file.parent.mkdir(parents=True, exist_ok=True)
    env_override["MAS_EVENTS_FILE"] = str(events_file)
    _run_pre_steps(lib_dir, req.pre)
    job = jobs.submit_job(
        endpoint=f"/api/libraries/{library_name}/run-mas",
        cmd=cmd,
        cwd=lib_dir,
        timeout=req.timeout,
        env_override=env_override or None,
        request_body={
            "query": req.query,
            "flavour": req.flavour,
            "mas_path": req.mas_path,
            "demo_ui_ref": req.demo_ui_ref,
        },
        cleanup_paths=cleanup_paths,
    )
    return {
        "job_id": job.id,
        "status": job.status.value,
        "command": job.command,
        "events_path": str(events_file),
    }


def _run_pre_steps(lib_dir: Path, steps: list[dict]) -> None:
    for step in steps or []:
        kind = str(step.get("type") or "shell")
        command = str(step.get("command") or (step.get("config") or {}).get("command") or "").strip()
        if kind != "shell" or not command:
            continue
        completed = subprocess.run(
            ["/bin/sh", "-c", command],
            cwd=str(lib_dir),
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise HTTPException(
                status_code=400,
                detail=f"pre shell failed ({completed.returncode}): {command}\n{completed.stderr}",
            )
