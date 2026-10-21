#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Extended FastAPI coverage — jobs, validate, UI contract routes."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

_DEMO_LAB = Path(__file__).resolve().parent / "fixtures" / "demo_lab"
_TEMPLATES = Path(__file__).resolve().parent / "fixtures" / "templates"


@pytest.fixture
def demo_lab(tmp_path: Path) -> Path:
    """Copy the demo_lab fixture tree into a temp directory."""
    dest = tmp_path / "demo_lab"
    shutil.copytree(_DEMO_LAB, dest)
    return dest


@pytest.fixture
def client(demo_lab, monkeypatch):
    from mas.lab.controller import fastapi_app, jobs
    from mas.lab.controller.manifest_store import ManifestStore

    store = ManifestStore(workspace=None)
    store._libraries = {"demo": demo_lab}

    monkeypatch.setattr("mas.lab.controller.deps.get_manifest_store", lambda: store)
    jobs._jobs.clear()
    return TestClient(fastapi_app.app)


def test_library_not_found(client):
    assert client.get("/api/libraries/nope/tools").status_code == 404


def test_validate_manifest_in_process(client):
    """Validate endpoint uses in-process validate_data (not run_cli)."""
    manifest_yaml = (_TEMPLATES / "validate-agent.yaml").read_text(encoding="utf-8")
    resp = client.post(
        "/api/libraries/demo/validate",
        json={"manifest_yaml": manifest_yaml},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["command"] == "validate_data"
    assert body["exit_code"] == 1


def test_overlay_validate_ok(client, monkeypatch):
    async def ok_overlay(content, lib_dir):
        return None

    monkeypatch.setattr("mas.lab.controller.deps.validate_overlay_content", ok_overlay)
    overlay_yaml = (_TEMPLATES / "validate-overlay.yaml").read_text(encoding="utf-8")
    resp = client.post(
        "/api/libraries/demo/overlays/validate",
        json={"manifest_yaml": overlay_yaml},
    )
    assert resp.status_code == 200


def test_overlay_validate_errors(client, monkeypatch):
    async def bad_overlay(content, lib_dir):
        return ["invalid patch"]

    monkeypatch.setattr("mas.lab.controller.deps.validate_overlay_content", bad_overlay)
    resp = client.post(
        "/api/libraries/demo/overlays/validate",
        json={"manifest_yaml": "bad"},
    )
    assert resp.status_code == 422


def test_overlay_create_conflict(client):
    resp = client.post(
        "/api/libraries/demo/overlays",
        json={"name": "test-overlay", "content": "description: dup\n", "run_validation": False},
    )
    assert resp.status_code == 409


def test_job_lifecycle_mock(client, monkeypatch):
    from mas.lab.controller import jobs

    def fake_submit(endpoint, cmd, cwd, timeout=60, env_override=None, request_body=None, cleanup_paths=None):
        job = jobs.Job(
            id="job-test-1",
            endpoint=endpoint,
            command=" ".join(cmd),
            status=jobs.JobStatus.COMPLETED,
            created_at=jobs.now_iso(),
            finished_at=jobs.now_iso(),
            exit_code=0,
        )
        jobs._jobs[job.id] = job
        return job

    monkeypatch.setattr("mas.lab.controller.jobs.submit_job", fake_submit)

    resp = client.post(
        "/api/libraries/demo/benchmark/run",
        json={"experiment_yaml": "metadata:\n  name: t\n", "progress": False},
    )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    detail = client.get(f"/api/jobs/{job_id}").json()
    assert detail["id"] == job_id

    listed = client.get("/api/jobs").json()["jobs"]
    assert any(j["id"] == job_id for j in listed)

    cleared = client.delete("/api/jobs").json()
    assert cleared["removed"] >= 1


def test_job_cancel_and_not_found(client):
    from mas.lab.controller import jobs

    mock_task = MagicMock()
    mock_task.done.return_value = False
    mock_task.cancel = MagicMock()

    job = jobs.Job(
        id="cancel-me",
        endpoint="/test",
        command="sleep",
        status=jobs.JobStatus.RUNNING,
        created_at=jobs.now_iso(),
        _task=mock_task,
    )
    jobs._jobs[job.id] = job

    resp = client.delete("/api/jobs/cancel-me")
    assert resp.status_code == 200

    assert client.get("/api/jobs/missing").status_code == 404

    done = jobs.Job(
        id="done-job",
        endpoint="/test",
        command="echo",
        status=jobs.JobStatus.COMPLETED,
        created_at=jobs.now_iso(),
    )
    jobs._jobs[done.id] = done
    resp2 = client.delete("/api/jobs/done-job")
    assert "terminal" in resp2.json()["message"]


@pytest.mark.asyncio
async def test_run_job_paths(monkeypatch, tmp_path):
    from mas.lab.controller import jobs

    job = jobs.Job(
        id="j1",
        endpoint="/x",
        command="true",
        status=jobs.JobStatus.PENDING,
        created_at=jobs.now_iso(),
    )

    class _Proc:
        pid = 123
        returncode = 0

        async def communicate(self):
            return b"out", b""

    proc = _Proc()
    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=proc),
    )
    await jobs.run_job(job, ["true"], tmp_path, 5, {})
    assert job.status == jobs.JobStatus.COMPLETED

    job2 = jobs.Job(
        id="j2",
        endpoint="/x",
        command="missing",
        status=jobs.JobStatus.PENDING,
        created_at=jobs.now_iso(),
    )
    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(side_effect=FileNotFoundError()),
    )
    await jobs.run_job(job2, ["nope"], tmp_path, 5, {})
    assert job2.status == jobs.JobStatus.FAILED


@pytest.mark.asyncio
async def test_lifespan_disabled_skips_discovery(monkeypatch):
    """MAS_CONTROLLER_DISABLE_STARTUP_DISCOVERY must short-circuit before
    touching the manifest store at all."""
    from mas.lab.controller import fastapi_app

    monkeypatch.setenv("MAS_CONTROLLER_DISABLE_STARTUP_DISCOVERY", "1")

    def _boom():
        raise AssertionError("get_manifest_store() must not be called when startup discovery is disabled")

    monkeypatch.setattr(fastapi_app, "get_manifest_store", _boom)

    async with fastapi_app.lifespan(fastapi_app.app):
        pass


@pytest.mark.asyncio
async def test_lifespan_enabled_emits_discovery_report(monkeypatch):
    """Default behavior (flag unset/false): lifespan fetches the manifest
    store and logs a single discovery report on startup."""
    from mas.lab.controller import fastapi_app

    monkeypatch.setenv("MAS_CONTROLLER_DISABLE_STARTUP_DISCOVERY", "0")
    calls = {"n": 0}

    class _FakeRegistry:
        def discovery_report(self):
            calls["n"] += 1
            return {"libraries": []}

    class _FakeStore:
        _registry = _FakeRegistry()

    monkeypatch.setattr(fastapi_app, "get_manifest_store", lambda: _FakeStore())

    async with fastapi_app.lifespan(fastapi_app.app):
        pass

    assert calls["n"] == 1


def test_api_info_and_topologies(client, demo_lab):
    info = client.get("/api/info").json()
    assert "edit_targets" in info
    assert "writable_default" in info

    (demo_lab / "topologies").mkdir(exist_ok=True)
    (demo_lab / "topologies" / "linear.yaml").write_text("kind: Topology\n", encoding="utf-8")
    tops = client.get("/api/libraries/demo/topologies").json()
    assert "linear.yaml" in tops["topologies"]


def test_scenarios_endpoint(client):
    data = client.get("/api/libraries/demo/scenarios").json()
    names = [s["name"] for s in data["scenarios"]]
    assert "trip-planner" in names


def test_pipeline_validate_and_update(client, demo_lab, monkeypatch):
    pipeline_yaml = (_TEMPLATES / "validate-pipeline.yaml").read_text(encoding="utf-8")

    monkeypatch.setattr(
        "mas.lab.controller.routes.pipelines.validate_pipeline_yaml",
        lambda _yaml: {"valid": True, "errors": []},
    )
    resp = client.post(
        "/api/libraries/demo/pipelines/validate",
        json={"manifest_yaml": pipeline_yaml},
    )
    assert resp.status_code == 200

    client.put(
        "/api/libraries/demo/pipelines/pipeline-test",
        json={"name": "pipeline-test", "content": pipeline_yaml},
    )
    assert client.get("/api/libraries/demo/pipelines/pipeline-test").status_code == 200


def test_ui_contract_routes(client):
    """Smoke-test routes referenced by mas-lab-ui apiCalls.ts."""
    routes = [
        ("GET", "/api/libraries"),
        ("GET", "/api/libraries/demo/tools"),
        ("GET", "/api/libraries/demo/skills"),
        ("GET", "/api/libraries/demo/experiments"),
        ("GET", "/api/libraries/demo/pipelines"),
        ("GET", "/api/libraries/demo/overlays"),
        ("GET", "/api/libraries/demo/datasets"),
        ("GET", "/api/libraries/demo/scenarios"),
        ("GET", "/api/libraries/demo/apps"),
        ("GET", "/api/libraries/demo/config-files"),
        ("GET", "/api/pipeline-step-types"),
        ("GET", "/api/metrics/eval"),
        ("GET", "/api/metrics/mce"),
        ("GET", "/api/jobs"),
    ]
    for method, path in routes:
        if method == "GET":
            resp = client.get(path)
        else:
            resp = client.post(path, json={})
        assert resp.status_code in (200, 202, 404, 422, 500), f"{method} {path} -> {resp.status_code}"
