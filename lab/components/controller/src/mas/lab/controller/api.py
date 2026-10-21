#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""ControllerAPI — pure Python backend for CLI and HTTP."""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from mas.lab.controller.manifest_store import ManifestStore
from mas.lab.controller.registry import WorkerRegistry
from mas.lab.controller.worker_model import WorkerKind, WorkerStatus
from mas.lab.controller.workers import (
    WorkerRunner,
    run_application_worker,
    run_benchmark_worker,
    run_experiment_worker,
    run_pipeline_worker,
)
from mas.runtime.spec.source import load_yaml_file

logger = logging.getLogger(__name__)

_INSTANCE: "ControllerAPI | None" = None


def set_controller_api(api: Optional["ControllerAPI"]) -> None:
    global _INSTANCE
    _INSTANCE = api


def peek_controller_api() -> "ControllerAPI | None":
    return _INSTANCE


def get_controller_api(workspace: Any = None) -> "ControllerAPI":
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = ControllerAPI(workspace)
    return _INSTANCE


class ControllerAPI:
    """Shared API surface for Unix-socket CLI and HTTP UI."""

    def __init__(self, workspace: Any = None) -> None:
        self.workspace = workspace
        self.workers = WorkerRegistry()
        self.runner = WorkerRunner(self.workers)
        self.manifests = ManifestStore(workspace)

    # -- Service ----------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        from mas.lab.controller import daemon as daemon_mod

        active = self.workers.list_workers(
            status=WorkerStatus.RUNNING,
        )
        return {
            "status": "ok",
            "workers": len(self.workers.list_workers()),
            "running": len(active),
            "libraries": len(self.manifests.libraries()),
            "sessions": daemon_mod._sessions.status(),
        }

    # -- Workers ----------------------------------------------------------------

    def list_workers(
        self,
        *,
        kind: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        kind_enum = WorkerKind(kind) if kind else None
        status_enum = WorkerStatus(status) if status else None
        return [w.to_job_summary() for w in self.workers.list_workers(kind=kind_enum, status=status_enum)]

    def get_worker(self, worker_id: str) -> Optional[Dict[str, Any]]:
        record = self.workers.get(worker_id)
        return record.to_job_detail() if record else None

    def cancel_worker(self, worker_id: str) -> bool:
        return self.runner.cancel(worker_id)

    def drop_worker(self, worker_id: str) -> bool:
        self.runner.cancel(worker_id)
        return self.workers.drop(worker_id)

    def drop_workers(self, *, origin: str | None = None, terminal_only: bool = True) -> int:
        from mas.lab.controller.worker_model import _ui_origin

        removed = 0
        for record in list(self.workers.list_workers()):
            if terminal_only and record.status not in {
                WorkerStatus.COMPLETED,
                WorkerStatus.FAILED,
                WorkerStatus.CANCELLED,
            }:
                continue
            job_origin = _ui_origin(record.spec)
            if origin is not None and job_origin != origin:
                continue
            if self.drop_worker(record.id):
                removed += 1
        return removed

    def submit_benchmark(self, spec: dict) -> Dict[str, Any]:
        record = run_benchmark_worker(self.workers, self.runner, spec)
        return {"worker_id": record.id, "job_id": record.id, "status": record.status.value}

    def submit_experiment(self, spec: dict) -> Dict[str, Any]:
        live_spec = {**spec, "live": True, "progress": False}
        record = run_experiment_worker(self.workers, self.runner, live_spec)
        return {"worker_id": record.id, "job_id": record.id, "status": record.status.value}

    def submit_application(self, spec: dict) -> Dict[str, Any]:
        record = run_application_worker(self.workers, self.runner, spec)
        return {"worker_id": record.id, "job_id": record.id, "status": record.status.value}

    def submit_pipeline(self, spec: dict) -> Dict[str, Any]:
        record = run_pipeline_worker(self.workers, self.runner, spec)
        return {"worker_id": record.id, "job_id": record.id, "status": record.status.value}

    # -- Jobs (UI alias) --------------------------------------------------------

    def list_jobs(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.list_workers(status=status)

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self.get_worker(job_id)

    # -- Libraries / manifests ----------------------------------------------------

    def list_libraries(self) -> List[Dict[str, str]]:
        self.manifests.refresh()
        return self.manifests.libraries()

    def list_runtime_runners(self) -> List[Dict[str, str]]:
        """Return registered ``mas.lab.runners`` plugins for UI / CLI."""
        from mas.lab.runners.factory import RunnerFactory

        return [{"id": rid, "label": rid} for rid in RunnerFactory.available()]

    def validate_manifest_yaml(self, manifest_yaml: str, *, base_dir: Path | None = None) -> Dict[str, Any]:
        from mas.lab.controller.manifest_validation import validate_manifest_yaml_content

        root = base_dir or Path.cwd()
        return validate_manifest_yaml_content(manifest_yaml, base_dir=root, resolve_refs=True)

    def list_experiments(self, library: str) -> List[Dict[str, Any]]:
        summaries: List[Dict[str, Any]] = []
        for item in self.manifests.list_yaml_resources(library, "experiments"):
            try:
                data = load_yaml_file(Path(item["file"]))
                meta = data.get("metadata") or data.get("experiment") or {}
                summaries.append(
                    {
                        "name": item["name"],
                        "description": meta.get("description", ""),
                        "version": str(meta.get("version", "")),
                        "scenarios": list((data.get("scenarios") or {}).keys()),
                        "dataset": str(data.get("dataset") or ""),
                    }
                )
            except Exception:
                summaries.append(
                    {
                        "name": item["name"],
                        "description": "",
                        "version": "",
                        "scenarios": [],
                        "dataset": "",
                    }
                )
        return summaries

    def get_experiment_content(self, library: str, name: str) -> Dict[str, str]:
        return {"name": name, "content": self.manifests.read_text(library, "experiments", name)}

    def save_experiment(self, library: str, name: str, content: str) -> None:
        self.manifests.write_text(library, "experiments", name, content)

    def delete_experiment(self, library: str, name: str) -> None:
        self.manifests.delete_resource(library, "experiments", name)

    def list_pipelines(self, library: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for item in self.manifests.list_yaml_resources(library, "pipelines"):
            try:
                data = load_yaml_file(Path(item["file"]))
                md = data.get("metadata") or {}
                steps = []
                for step in data.get("steps") or []:
                    steps.append(
                        {
                            "name": step.get("name", step.get("id", "")),
                            "type": step.get("type", ""),
                            "depends_on": list(step.get("depends_on") or []),
                        }
                    )
                out.append(
                    {
                        "filename": item["name"],
                        "name": md.get("name") or item["name"],
                        "description": md.get("description", ""),
                        "steps": steps,
                        "experiment": str(data.get("experiment") or ""),
                    }
                )
            except Exception:
                out.append(
                    {
                        "filename": item["name"],
                        "name": item["name"],
                        "description": "",
                        "steps": [],
                        "experiment": "",
                    }
                )
        return out

    def get_pipeline_content(self, library: str, name: str) -> Dict[str, str]:
        return {"name": name, "content": self.manifests.read_text(library, "pipelines", name)}

    def save_pipeline(self, library: str, name: str, content: str) -> None:
        self.manifests.write_text(library, "pipelines", name, content)

    def delete_pipeline(self, library: str, name: str) -> None:
        self.manifests.delete_resource(library, "pipelines", name)

    def list_overlays(self, library: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for item in self.manifests.list_yaml_resources(library, "overlays"):
            try:
                data = load_yaml_file(Path(item["file"]))
                out.append({"name": item["name"], "description": data.get("description", "")})
            except Exception:
                out.append({"name": item["name"]})
        return out

    def get_overlay_content(self, library: str, name: str) -> Dict[str, str]:
        return {"name": name, "content": self.manifests.read_text(library, "overlays", name)}

    def save_overlay(self, library: str, name: str, content: str) -> None:
        self.manifests.write_text(library, "overlays", name, content)

    def delete_overlay(self, library: str, name: str) -> None:
        self.manifests.delete_resource(library, "overlays", name)

    def list_datasets(self, library: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for item in self.manifests.list_yaml_resources(library, "datasets"):
            try:
                data = load_yaml_file(Path(item["file"]))
                md = data.get("metadata") or {}
                out.append({"name": item["name"], "description": md.get("description", "")})
            except Exception:
                out.append({"name": item["name"], "description": ""})
        return out

    def get_dataset_content(self, library: str, name: str) -> Dict[str, str]:
        return {"name": name, "content": self.manifests.read_text(library, "datasets", name)}

    def save_dataset(self, library: str, name: str, content: str) -> None:
        self.manifests.write_text(library, "datasets", name, content)

    def delete_dataset(self, library: str, name: str) -> None:
        self.manifests.delete_resource(library, "datasets", name)

    def list_scenarios(self, library: str) -> List[Dict[str, str]]:
        return [
            {"name": item["name"], "path": item["path"]}
            for item in self.manifests.list_yaml_resources(library, "scenarios")
        ]

    def config_files(self, library: str) -> Dict[str, Dict[str, str]]:
        return self.manifests.config_files(library)

    def list_tools(self, library: str) -> List[Dict[str, str]]:
        from mas.lab.controller.artifact_discovery import discover_tools

        return discover_tools(self.manifests.library_root(library))

    def list_skills(self, library: str) -> List[Dict[str, str]]:
        from mas.lab.controller.artifact_discovery import discover_skills

        return discover_skills(self.manifests.library_root(library))

    def pipeline_step_types(self) -> Dict[str, Any]:
        from mas.lab.controller.lab_registry import get_lab_registry

        return get_lab_registry(self.workspace).pipeline_step_types()

    def registry_catalog(self) -> Dict[str, Any]:
        from mas.lab.controller.lab_registry import get_lab_registry

        return get_lab_registry(self.workspace).catalog()

    def run_agent_job(self, library: str, body: dict) -> Dict[str, Any]:
        root = self.manifests.library_root(library)
        agents_dir = root / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", dir=agents_dir, delete=False) as fh:
            fh.write(body.get("manifest_yaml") or "")
            spec_path = fh.name
        spec = {
            "prompt": body.get("query") or "",
            "spec_path": spec_path,
            "config": {},
            "endpoint": f"/api/libraries/{library}/run",
            "command": f"run agent ({library})",
        }
        return self.submit_application(spec)

    def run_mas_job(self, library: str, body: dict) -> Dict[str, Any]:
        root = self.manifests.library_root(library)
        mas_dir = root / "apps"
        mas_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", dir=mas_dir, delete=False) as fh:
            fh.write(body.get("manifest_yaml") or "")
            spec_path = fh.name
        spec = {
            "prompt": body.get("query") or "",
            "spec_path": spec_path,
            "config": {"overlays": body.get("overlays") or []},
            "endpoint": f"/api/libraries/{library}/run-mas",
            "command": f"run mas ({library})",
        }
        return self.submit_application(spec)

    def run_benchmark_job(self, library: str, body: dict) -> Dict[str, Any]:
        content = body.get("experiment_yaml") or ""
        root = self.manifests.library_root(library)
        exp_dir = root / "experiments"
        exp_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", dir=exp_dir, delete=False) as fh:
            fh.write(content)
            experiment_yaml = fh.name
        spec = {
            "experiment_yaml": experiment_yaml,
            "progress": body.get("progress", True),
            "max_runs": body.get("n_runs"),
            "endpoint": f"/api/libraries/{library}/benchmark/run",
        }
        result = self.submit_benchmark(spec)
        result["command"] = f"mas-lab benchmark run {experiment_yaml}"
        return result

    def run_pipeline_job(self, library: str, body: dict) -> Dict[str, Any]:
        root = self.manifests.library_root(library)
        pipe_dir = root / "pipelines"
        pipe_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", dir=pipe_dir, delete=False) as fh:
            fh.write(body.get("pipeline_yaml") or "")
            pipeline_yaml = fh.name
        spec = {
            "pipeline_yaml": pipeline_yaml,
            "only": body.get("only"),
            "command": f"pipeline run {pipeline_yaml}",
        }
        return self.submit_pipeline(spec)
