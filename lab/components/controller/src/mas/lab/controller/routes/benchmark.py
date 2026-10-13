#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Benchmark run endpoints and global experiment results."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse

from mas.lab.controller.deps import build_tree
from mas.lab.controller.models import (
    BenchmarkAnalyzeRequest,
    BenchmarkExportRequest,
    BenchmarkImportRequest,
    BenchmarkRunRequest,
)
from mas.lab.controller.routes._api import MAS_LAB_ROOT, deps, jobs, LIBRARIES_DIR, validate_pipeline_yaml

router = APIRouter()


@router.post("/api/libraries/{library_name}/benchmark/run", tags=["Libraries"], status_code=202)
async def benchmark_run(library_name: str, req: BenchmarkRunRequest):
    """Run a benchmark experiment. Returns a job_id for polling."""
    lib_dir = deps.get_library_path(library_name)

    cleanup_paths: list[Path] = []

    # If the value looks like YAML content (has newlines), write to a temp file
    if "\n" in req.experiment_yaml:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", prefix="experiment-", delete=False, dir=str(lib_dir)
        )
        tmp.write(req.experiment_yaml)
        tmp.close()
        experiment_path = Path(tmp.name).name
        cleanup_paths.append(Path(tmp.name))
    else:
        experiment_path = req.experiment_yaml

    cmd = ["mas-lab", "benchmark", "run", experiment_path]
    if req.progress:
        cmd.append("--progress")
    if req.force:
        cmd.append("--force")
    if req.max_runs is not None:
        cmd += ["--max-runs", str(req.max_runs)]
    if req.flavour:
        cmd += ["--flavour", req.flavour]
    job = jobs.submit_job(
        endpoint=f"/api/libraries/{library_name}/benchmark/run",
        cmd=cmd,
        cwd=lib_dir,
        timeout=req.timeout,
        request_body=req.model_dump(),
        cleanup_paths=cleanup_paths,
    )
    return {"job_id": job.id, "status": job.status.value, "command": job.command}


@router.post("/api/libraries/{library_name}/benchmark/analyze", tags=["Libraries"], status_code=202)
async def benchmark_analyze(library_name: str, req: BenchmarkAnalyzeRequest):
    """Regenerate statistics/plots for an existing benchmark run."""
    lib_dir = deps.get_library_path(library_name)
    cmd = ["mas-lab", "benchmark", "analyze", req.benchmark_id]
    if req.experiment_yaml:
        cmd += ["--experiment-yaml", req.experiment_yaml]
    job = jobs.submit_job(
        endpoint=f"/api/libraries/{library_name}/benchmark/analyze",
        cmd=cmd,
        cwd=lib_dir,
        timeout=req.timeout,
        request_body=req.model_dump(),
    )
    return {"job_id": job.id, "status": job.status.value, "command": job.command}


@router.post("/api/libraries/{library_name}/benchmark/export", tags=["Libraries"], status_code=202)
async def benchmark_export(library_name: str, req: BenchmarkExportRequest):
    """Export a benchmark run to a portable .tar.gz archive."""
    lib_dir = deps.get_library_path(library_name)
    cmd = ["mas-lab", "benchmark", "export", req.benchmark_id]
    if req.output:
        cmd += ["--output", req.output]
    if not req.include_trace_cache:
        cmd.append("--no-trace-cache")
    if req.dry_run:
        cmd.append("--dry-run")
    job = jobs.submit_job(
        endpoint=f"/api/libraries/{library_name}/benchmark/export",
        cmd=cmd,
        cwd=lib_dir,
        timeout=req.timeout,
        request_body=req.model_dump(),
    )
    return {"job_id": job.id, "status": job.status.value, "command": job.command}


@router.get("/api/libraries/{library_name}/benchmark/download", tags=["Libraries"])
async def benchmark_download(library_name: str, benchmark_id: str):
    """Export and download a benchmark run as a .tar.gz file."""
    import subprocess

    lib_dir = deps.get_library_path(library_name)
    tmp_dir = Path(tempfile.mkdtemp())
    out_path = tmp_dir / f"{benchmark_id}.tar.gz"

    cmd = ["mas-lab", "benchmark", "export", benchmark_id, "--output", str(out_path)]
    result = subprocess.run(cmd, cwd=lib_dir, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        detail = result.stderr.strip().split("\n")[-1] if result.stderr else "Export failed"
        raise HTTPException(status_code=400, detail=detail)

    if not out_path.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail="Export produced no output file")

    from starlette.background import BackgroundTask

    return FileResponse(
        out_path,
        filename=out_path.name,
        media_type="application/gzip",
        background=BackgroundTask(shutil.rmtree, tmp_dir, True),
    )


@router.post("/api/libraries/{library_name}/benchmark/import", tags=["Libraries"], status_code=202)
async def benchmark_import(library_name: str, req: BenchmarkImportRequest):
    """Import a benchmark archive produced by benchmark export."""
    lib_dir = deps.get_library_path(library_name)
    cmd = ["mas-lab", "benchmark", "import", req.tarball]
    if req.output_dir:
        cmd += ["--output-dir", req.output_dir]
    if req.trace_cache_dir:
        cmd += ["--trace-cache", req.trace_cache_dir]
    if req.dry_run:
        cmd.append("--dry-run")
    job = jobs.submit_job(
        endpoint=f"/api/libraries/{library_name}/benchmark/import",
        cmd=cmd,
        cwd=lib_dir,
        timeout=req.timeout,
        request_body=req.model_dump(),
    )
    return {"job_id": job.id, "status": job.status.value, "command": job.command}


@router.post("/api/libraries/{library_name}/benchmark/upload-import", tags=["Libraries"], status_code=202)
async def benchmark_upload_import(library_name: str, file: UploadFile):
    """Accept a .tar.gz upload and import it as a benchmark run."""
    lib_dir = deps.get_library_path(library_name)

    tmp = tempfile.NamedTemporaryFile(
        suffix=".tar.gz", prefix="bench-import-", delete=False, dir=str(lib_dir)
    )
    try:
        contents = await file.read()
        tmp.write(contents)
        tmp.close()

        cmd = ["mas-lab", "benchmark", "import", tmp.name]
        job = jobs.submit_job(
            endpoint=f"/api/libraries/{library_name}/benchmark/upload-import",
            cmd=cmd,
            cwd=lib_dir,
            timeout=120,
            request_body={"filename": file.filename},
            cleanup_paths=[Path(tmp.name)],
        )
        return {"job_id": job.id, "status": job.status.value, "command": job.command}
    except Exception:
        Path(tmp.name).unlink(missing_ok=True)
        raise


@router.get("/api/experiments", tags=["Benchmark"])
async def list_experiments():
    """List all experiment runs with metadata."""
    mas_lab_root = MAS_LAB_ROOT
    labs_dir = mas_lab_root / "labs"
    if not labs_dir.exists():
        return {"experiments": []}

    experiments = []
    for d in sorted(labs_dir.iterdir()):
        if not d.is_dir():
            continue
        entry: dict = {"name": d.name}
        meta_file = d / "metadata.yaml"
        if meta_file.exists():
            import yaml
            meta = yaml.safe_load(meta_file.read_text(encoding="utf-8"))
            entry["status"] = meta.get("status", "unknown")
            entry["description"] = meta.get("experiment_description", "")
            entry["started_at"] = meta.get("started_at")
            entry["completed_at"] = meta.get("completed_at")
            entry["duration_seconds"] = meta.get("duration_seconds")
            entry["n_scenarios"] = meta.get("n_scenarios")
            entry["completed_scenarios"] = meta.get("completed_scenarios")
            entry["failed_scenarios"] = meta.get("failed_scenarios")
        experiments.append(entry)
    return {"experiments": experiments}


@router.get("/api/experiments/definitions", tags=["Libraries"])
async def list_all_experiment_definitions():
    """All experiment definitions across discovered libraries."""
    experiments = deps.get_manifest_store()._registry.list_all_experiments()
    return {"experiments": experiments}


@router.get("/api/experiments/{experiment_name}", tags=["Benchmark"])
async def get_experiment(experiment_name: str):
    """Return experiment metadata and file tree."""
    mas_lab_root = MAS_LAB_ROOT
    exp_dir = mas_lab_root / "labs" / experiment_name
    if not exp_dir.exists() or not exp_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Experiment '{experiment_name}' not found")

    metadata = {}
    meta_file = exp_dir / "metadata.yaml"
    if meta_file.exists():
        import yaml
        metadata = yaml.safe_load(meta_file.read_text(encoding="utf-8"))

    tree = build_tree(exp_dir, exp_dir)

    return {
        "name": experiment_name,
        "metadata": metadata,
        "tree": tree,
    }


@router.get("/api/experiments/{experiment_name}/file", tags=["Benchmark"])
async def get_experiment_file(experiment_name: str, path: str):
    """Read a file from an experiment's output directory.

    Pass the relative path as a query parameter, e.g.
    ``?path=metrics/answer_relevancy.csv``
    """
    mas_lab_root = MAS_LAB_ROOT
    exp_dir = mas_lab_root / "labs" / experiment_name
    if not exp_dir.exists():
        raise HTTPException(status_code=404, detail=f"Experiment '{experiment_name}' not found")

    file_path = (exp_dir / path).resolve()
    # Allow paths inside the experiment dir or the trace/data cache (symlinked runs).
    # Trace symlinks may point to the XDG cache dir (~/.cache/mas/) which is
    # separate from MAS_LAB_ROOT (~/.local/share/mas/).
    from mas.runtime.xdg import mas_cache_root
    allowed_roots = [
        str(exp_dir.resolve()),
        str((mas_lab_root / "data").resolve()),
        str(mas_cache_root().resolve()),
    ]
    if not any(str(file_path).startswith(root) for root in allowed_roots):
        raise HTTPException(status_code=403, detail="Path traversal not allowed")
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    # For text files, return content inline; for others, return as download
    text_suffixes = {".json", ".jsonl", ".yaml", ".yml", ".csv", ".txt", ".html", ".md", ".fingerprint", ".svg"}
    if file_path.suffix in text_suffixes:
        content = file_path.read_text(encoding="utf-8")
        return {"path": path, "content": content}

    # Raster images are inlined too: the UI fetches this endpoint as JSON and
    # builds a `data:` URI, so return the bytes base64-encoded rather than as a
    # binary FileResponse (which the JSON client cannot parse).
    raster_suffixes = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
    if file_path.suffix.lower() in raster_suffixes:
        import base64

        content = base64.b64encode(file_path.read_bytes()).decode("ascii")
        return {"path": path, "content": content}

    return FileResponse(file_path, filename=file_path.name)


@router.delete("/api/experiments/{experiment_name}", tags=["Benchmark"])
async def delete_experiment(experiment_name: str):
    """Delete an experiment's results and clear the trace cache."""
    mas_lab_root = MAS_LAB_ROOT
    exp_dir = mas_lab_root / "labs" / experiment_name
    if not exp_dir.exists() or not exp_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Experiment '{experiment_name}' not found")

    shutil.rmtree(exp_dir)

    cache_dir = mas_lab_root / "data" / "trace-cache"
    cache_cleared = False
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
        cache_cleared = True

    return {
        "deleted": experiment_name,
        "experiment_path": str(exp_dir),
        "trace_cache_cleared": cache_cleared,
    }
