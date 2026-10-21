#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Run lifecycle, persistence across a simulated restart, SSE, delete."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.lab.controller.run_store import RunStore
from scope_fixtures import build_store, copy_scope_fixtures, make_client


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    return make_client(build_store(copy_scope_fixtures(tmp_path)), monkeypatch)


def _complete_job_immediately(monkeypatch):
    from mas.lab.controller import jobs as jobs_mod
    from mas.lab.controller.jobs import Job, JobStatus, now_iso

    def fake_submit(**kwargs):
        job = Job(
            id="job-test",
            endpoint=kwargs.get("endpoint", "/api/runs"),
            command=kwargs.get("cmd") and " ".join(kwargs["cmd"]) or "true",
            status=JobStatus.COMPLETED,
            created_at=now_iso(),
            started_at=now_iso(),
            finished_at=now_iso(),
            exit_code=0,
        )
        jobs_mod._jobs[job.id] = job
        return job

    monkeypatch.setattr(jobs_mod, "submit_job", fake_submit)


def test_run_lifecycle(client, monkeypatch):
    _complete_job_immediately(monkeypatch)
    started = client.post(
        "/api/runs",
        json={"scope": "library:versioned_lib", "kind": "application", "item": "plain", "query": "hi"},
    )
    assert started.status_code == 202, started.text
    body = started.json()
    run_id = body["id"]
    assert body["status"] == "completed"
    assert body.get("job_id")
    listed = client.get("/api/runs").json()["runs"]
    assert any(r["id"] == run_id for r in listed)
    detail = client.get(f"/api/runs/{run_id}")
    assert detail.status_code == 200
    events = client.get(f"/api/runs/{run_id}/events")
    assert events.status_code == 200
    assert "text/event-stream" in events.headers.get("content-type", "")
    deleted = client.delete(f"/api/runs/{run_id}")
    assert deleted.status_code == 200
    assert client.get(f"/api/runs/{run_id}").status_code == 404


def test_completed_run_registers_artifacts(tmp_path: Path, monkeypatch):
    from mas.lab.controller.artifacts import ArtifactRegistry, set_artifact_registry

    set_artifact_registry(ArtifactRegistry())
    roots = copy_scope_fixtures(tmp_path)
    out = roots["versioned_lib"] / "output"
    out.mkdir()
    (out / "metrics.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    http = make_client(build_store(roots), monkeypatch)
    _complete_job_immediately(monkeypatch)
    started = http.post(
        "/api/runs",
        json={"scope": "library:versioned_lib", "kind": "application", "item": "plain"},
    )
    assert started.status_code == 202, started.text
    run_id = started.json()["id"]
    arts = http.get(f"/api/runs/{run_id}/artifacts").json()["artifacts"]
    assert arts
    assert all("artifact_id" in row and "name" in row for row in arts)
    blob = str(arts)
    assert str(roots["versioned_lib"]) not in blob
    csv = next(row for row in arts if row["name"] == "metrics.csv")
    fetched = http.get(f"/api/artifacts/{csv['artifact_id']}")
    assert fetched.status_code == 200
    assert "1,2" in fetched.text


def test_run_command_uses_mas_ctl(tmp_path: Path, monkeypatch):
    from mas.lab.controller.routes.runs import StartRunRequest, _command_for_run

    store = build_store(copy_scope_fixtures(tmp_path))
    monkeypatch.setattr("mas.lab.controller.deps.get_manifest_store", lambda: store)
    cmd, cleanup = _command_for_run(
        StartRunRequest(scope="library:versioned_lib", kind="application", item="plain", query="hi"),
        store,
        cwd=tmp_path,
    )
    assert cmd[:2] == ["mas-ctl", "run-mas"]
    assert "-q" in cmd and "hi" in cmd
    assert cleanup == []


def test_labs_dir_outputs_appear_as_runs(tmp_path: Path, monkeypatch):
    from mas.lab.controller.artifacts import ArtifactRegistry, set_artifact_registry
    from mas.lab.controller.run_store import init_run_store

    init_run_store(None)
    set_artifact_registry(ArtifactRegistry())
    outputs = tmp_path / "labs-out"
    tree = outputs / "only"
    tree.mkdir(parents=True)
    (tree / "heatmap.png").write_bytes(b"png")
    monkeypatch.setenv("MAS_LABS_ROOT", str(outputs))
    http = make_client(build_store(copy_scope_fixtures(tmp_path)), monkeypatch)
    listed = http.get("/api/runs").json()["runs"]
    adopted = [row for row in listed if row["item"] == "only" and row["scope"] == "lab:lonely"]
    assert len(adopted) == 1
    assert adopted[0]["status"] == "completed"
    arts = http.get(f"/api/runs/{adopted[0]['id']}/artifacts").json()["artifacts"]
    assert any(row["name"] == "heatmap.png" for row in arts)
    again = http.get("/api/runs").json()["runs"]
    assert len([row for row in again if row["item"] == "only" and row["scope"] == "lab:lonely"]) == 1


def test_persistence_across_restart(tmp_path: Path):
    db = tmp_path / "runs.sqlite"
    first = RunStore(db)
    from mas.lab.controller.run_store import new_run

    run = new_run(scope="library:versioned_lib", kind="application", item="plain")
    run.status = "completed"
    first.save(run)
    run_id = run.id
    first.close()
    second = RunStore(db)
    loaded = second.get(run_id)
    assert loaded is not None
    assert loaded.status == "completed"
    assert loaded.item == "plain"
    second.close()
