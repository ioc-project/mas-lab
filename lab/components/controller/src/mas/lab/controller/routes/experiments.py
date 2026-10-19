#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Library-scoped experiment definition CRUD endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from mas.lab.controller.api import get_controller_api
from mas.lab.controller.models import ExperimentRunRequest, SaveExperimentRequest
from mas.lab.controller.routes._api import deps
from mas.lab.controller.routes.run import _run_pre_steps

router = APIRouter()


def resolve_experiment_path(lib_dir: Path, name: str) -> Path:
    """Resolve an experiment by path, filename, stem, or YAML ``experiment.name``."""
    raw = Path(name)
    candidates: list[Path] = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.append(lib_dir / name)
        candidates.append(lib_dir / "experiments" / name)
        if not name.endswith((".yaml", ".yml")):
            candidates.extend(
                [
                    lib_dir / f"{name}.yaml",
                    lib_dir / "experiments" / f"{name}.yaml",
                    lib_dir / "experiment.yaml",
                    lib_dir / "experiments" / "experiment.yaml",
                ]
            )
    for path in candidates:
        if path.is_file():
            return path
    wanted = name
    if wanted.endswith(".yaml"):
        wanted = wanted[:-5]
    elif wanted.endswith(".yml"):
        wanted = wanted[:-4]
    for path in _experiment_yaml_files(lib_dir):
        if path.stem == wanted or path.name == name:
            return path
        if _experiment_yaml_name(path) == wanted:
            return path
    raise HTTPException(status_code=404, detail=f"Experiment '{name}' not found")


def _experiment_yaml_files(lib_dir: Path):
    if not lib_dir.is_dir():
        return
    for path in (lib_dir / "experiment.yaml", lib_dir / "experiment.yml"):
        if path.is_file():
            yield path
    experiments = lib_dir / "experiments"
    if experiments.is_dir():
        yield from sorted(experiments.glob("*.yaml"))
        yield from sorted(experiments.glob("*.yml"))
        yield from sorted(experiments.glob("**/experiment.yaml"))
        yield from sorted(experiments.glob("**/experiment.yml"))


def _experiment_yaml_name(path: Path) -> str:
    try:
        from mas.runtime.spec.source import load_yaml_file

        doc = load_yaml_file(path)
    except Exception:
        return path.stem
    block = doc.get("experiment") if isinstance(doc, dict) else None
    if isinstance(block, dict) and block.get("name"):
        return str(block["name"])
    return path.stem


@router.post("/api/libraries/{library_name}/experiment/run", tags=["Libraries"], status_code=202)
async def experiment_run(library_name: str, req: ExperimentRunRequest):
    """Start an in-process experiment worker that streams live to demo-ui."""
    lib_dir = deps.get_library_path(library_name)
    _run_pre_steps(lib_dir, req.pre)
    experiment_path = resolve_experiment_path(lib_dir, req.experiment_yaml)
    spec = {
        "experiment_yaml": str(experiment_path),
        "progress": False,
        "live": True,
        "single_run": True,
        "force": req.force,
        "max_runs": req.max_runs or 1,
        "query": req.query,
        "infra_refs": list(req.infra_refs or []),
        "demo_ui_ingest_url": req.demo_ui_ingest_url,
        "demo_ui_ref": req.demo_ui_ref or {},
        "endpoint": f"/api/libraries/{library_name}/experiment/run",
        "command": f"mas-lab experiment run {experiment_path}",
    }
    result = get_controller_api().submit_experiment(spec)
    result["command"] = spec["command"]
    return result


@router.get("/api/libraries/{library_name}/experiments", tags=["Libraries"])
async def list_library_experiments(library_name: str):
    """List available experiment definitions with metadata."""
    experiments = deps.get_manifest_store().list_experiments(library_name)
    for exp in experiments:
        exp.setdefault("library", library_name)
    return {"experiments": experiments}


@router.post("/api/libraries/{library_name}/experiments", tags=["Libraries"], status_code=201)
async def create_experiment(library_name: str, req: SaveExperimentRequest):
    """Create a new experiment definition in the library's experiments/ directory."""
    lib_dir = deps.get_library_path(library_name)
    exp_dir = lib_dir / "experiments"
    exp_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{req.name}.yaml" if not req.name.endswith(".yaml") else req.name
    file_path = exp_dir / filename

    if file_path.exists():
        raise HTTPException(status_code=409, detail=f"Experiment '{req.name}' already exists")

    file_path.write_text(req.content, encoding="utf-8")
    deps.get_manifest_store().invalidate()
    return {"name": req.name, "filename": filename}


@router.get("/api/libraries/{library_name}/experiments/{experiment_name}", tags=["Libraries"])
async def get_library_experiment(library_name: str, experiment_name: str):
    """Return the content of a specific experiment definition."""
    store = deps.get_manifest_store()
    lib_dir = deps.get_library_path(library_name)
    filename = f"{experiment_name}.yaml" if not experiment_name.endswith(".yaml") else experiment_name
    candidates = [
        lib_dir / "experiments" / filename,
        lib_dir / filename,
    ]
    candidates.extend(
        p for p in store._registry._iter_experiment_files(lib_dir)
        if p.name == filename or p.stem == experiment_name
    )
    file_path = next((p for p in candidates if p.exists()), None)

    if file_path is None:
        raise HTTPException(status_code=404, detail=f"Experiment '{experiment_name}' not found")

    return {
        "name": experiment_name,
        "filename": file_path.name,
        "content": file_path.read_text(encoding="utf-8"),
    }


@router.put("/api/libraries/{library_name}/experiments/{experiment_name}", tags=["Libraries"])
async def update_experiment(library_name: str, experiment_name: str, req: SaveExperimentRequest):
    """Update an existing experiment definition. Supports rename via req.name."""
    lib_dir = deps.get_library_path(library_name)
    exp_dir = lib_dir / "experiments"

    old_filename = f"{experiment_name}.yaml" if not experiment_name.endswith(".yaml") else experiment_name
    old_path = exp_dir / old_filename

    if not old_path.exists():
        raise HTTPException(status_code=404, detail=f"Experiment '{experiment_name}' not found")

    new_filename = f"{req.name}.yaml" if not req.name.endswith(".yaml") else req.name
    new_path = exp_dir / new_filename

    if req.name != experiment_name and new_path.exists():
        raise HTTPException(status_code=409, detail=f"Experiment '{req.name}' already exists")

    if old_path != new_path:
        old_path.unlink()
    new_path.write_text(req.content, encoding="utf-8")
    deps.get_manifest_store().invalidate()
    return {"name": req.name, "filename": new_filename}


@router.delete("/api/libraries/{library_name}/experiments/{experiment_name}", tags=["Libraries"])
async def delete_library_experiment(library_name: str, experiment_name: str):
    """Delete an experiment definition."""
    lib_dir = deps.get_library_path(library_name)
    filename = f"{experiment_name}.yaml" if not experiment_name.endswith(".yaml") else experiment_name
    file_path = lib_dir / "experiments" / filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Experiment '{experiment_name}' not found")

    file_path.unlink()
    deps.get_manifest_store().invalidate()
    return {"deleted": experiment_name}
