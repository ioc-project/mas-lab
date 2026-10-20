#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for FastAPI app using demo fixture data.

The demo data lives in ``fixtures/demo_lab/`` — a self-contained,
version-controlled lab tree that is copied into ``tmp_path`` for each test
so CRUD operations never mutate the checked-in fixtures.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_DEMO_LAB = Path(__file__).resolve().parent / "fixtures" / "demo_lab"
_TEMPLATES = Path(__file__).resolve().parent / "fixtures" / "templates"


def _fake_store(lab: Path):
    from mas.lab.controller.lab_registry import LabRegistry
    from mas.lab.controller.manifest_store import ManifestStore

    # Normal construction: the autouse `_controller_test_defaults` fixture sets
    # MAS_LAB_REGISTRY_EAGER_REFRESH=0, so LabRegistry() does no discovery here.
    # Only one in-memory demo library is needed, injected after construction.
    registry = LabRegistry(workspace=None)
    registry._libraries = {"demo": lab}
    registry._library_sources = {"demo": "test-fixture"}
    store = ManifestStore(registry=registry)
    store._libraries = {"demo": lab}
    return store


@pytest.fixture
def demo_lab(tmp_path: Path) -> Path:
    """Copy the demo_lab fixture tree into a temp directory."""
    dest = tmp_path / "demo_lab"
    shutil.copytree(_DEMO_LAB, dest)
    return dest


@pytest.fixture
def client(demo_lab, monkeypatch):
    from mas.lab.controller import fastapi_app

    store = _fake_store(demo_lab)

    monkeypatch.setattr(
        "mas.lab.controller.deps.get_manifest_store",
        lambda: store,
    )
    return TestClient(fastapi_app.app)


# -- Health -------------------------------------------------------------------

def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/api/health").json() == {"status": "ok"}


def test_ui_plugins_route(client):
    res = client.get("/api/plugins")
    assert res.status_code == 200
    assert "plugins" in res.json()


# -- Libraries ----------------------------------------------------------------

def test_list_libraries(client):
    data = client.get("/api/libraries").json()
    assert len(data["libraries"]) == 1
    assert data["libraries"][0]["dir"] == "demo"


# -- Tools & Skills -----------------------------------------------------------

def test_tools_and_skills(client):
    tools = client.get("/api/libraries/demo/tools").json()["tools"]
    tool_names = {t["name"] for t in tools}
    assert "global/calc" in tool_names
    assert "global/web-search" in tool_names

    skills = client.get("/api/libraries/demo/skills").json()["skills"]
    skill_names = {s["name"] for s in skills}
    assert "global/answer-formatting" in skill_names


def test_read_library_document(client):
    data = client.get(
        "/api/libraries/demo/documents",
        params={"path": "apps/trip-planner/mas.yaml"},
    ).json()
    assert "workflow:" in data["content"]
    assert data["path"] == "apps/trip-planner/mas.yaml"


def test_read_library_document_rejects_escape(client):
    resp = client.get(
        "/api/libraries/demo/documents",
        params={"path": "../outside.yaml"},
    )
    assert resp.status_code == 400


def test_read_library_document_missing(client):
    resp = client.get(
        "/api/libraries/demo/documents",
        params={"path": "no-such.yaml"},
    )
    assert resp.status_code == 404


# -- Experiments CRUD ---------------------------------------------------------

def test_experiments_crud(client, demo_lab):
    listed = client.get("/api/libraries/demo/experiments").json()["experiments"]
    assert any(e["name"] == "trip-planner-design-pattern-experiment" for e in listed)

    detail = client.get(
        "/api/libraries/demo/experiments/trip-planner-design-pattern-experiment",
    ).json()
    assert "trip-planner-design-pattern-experiment" in detail["content"]

    newone_yaml = (_TEMPLATES / "new-experiment.yaml").read_text(encoding="utf-8")
    resp = client.post(
        "/api/libraries/demo/experiments",
        json={"name": "newone", "content": newone_yaml},
    )
    assert resp.status_code == 201
    assert (demo_lab / "experiments" / "newone.yaml").exists()

    # Regression guard: the list endpoint must reflect the write immediately,
    # not a listing cached from before the create (invalidate() on write).
    # `filename` (unlike `name`, which is parsed from the YAML content and may
    # differ from the on-disk name) is a reliable proxy for the written file.
    after_create = client.get("/api/libraries/demo/experiments").json()["experiments"]
    assert any(e["filename"] == "newone.yaml" for e in after_create)

    client.put(
        "/api/libraries/demo/experiments/newone",
        json={"name": "renamed", "content": newone_yaml.replace("name: new-experiment", "name: renamed")},
    )
    assert (demo_lab / "experiments" / "renamed.yaml").exists()

    after_rename = client.get("/api/libraries/demo/experiments").json()["experiments"]
    after_rename_filenames = {e["filename"] for e in after_rename}
    assert "renamed.yaml" in after_rename_filenames
    assert "newone.yaml" not in after_rename_filenames

    client.delete("/api/libraries/demo/experiments/renamed")
    assert not (demo_lab / "experiments" / "renamed.yaml").exists()

    after_delete = client.get("/api/libraries/demo/experiments").json()["experiments"]
    assert "renamed.yaml" not in {e["filename"] for e in after_delete}


# -- Pipelines & Overlays ----------------------------------------------------

def test_pipelines_and_overlays(client):
    pipes = client.get("/api/libraries/demo/pipelines").json()["pipelines"]
    pipe_names = {p["name"] for p in pipes}
    assert "pipeline-test" in pipe_names

    overlays = client.get("/api/libraries/demo/overlays").json()["overlays"]
    overlay_names = {o["name"] for o in overlays}
    assert "cot-moderator" in overlay_names
    assert "react-moderator" in overlay_names

    detail = client.get("/api/libraries/demo/overlays/cot-moderator").json()
    assert "cot-moderator" in detail["content"]


# -- Datasets -----------------------------------------------------------------

def test_datasets(client):
    datasets = client.get("/api/libraries/demo/datasets").json()["datasets"]
    dataset_names = {d["name"] for d in datasets}
    assert "queries.yaml" in dataset_names
    assert "benchmark.yaml" in dataset_names


# -- MAS Resources (apps) ----------------------------------------------------

def test_mas_resources(client, demo_lab):
    listed = client.get("/api/libraries/demo/apps").json()["mas_resources"]
    assert "trip-planner" in listed

    detail = client.get("/api/libraries/demo/apps/trip-planner").json()
    assert detail["mas_name"] == "trip-planner"

    mas_yaml = (_TEMPLATES / "new-mas.yaml").read_text(encoding="utf-8")
    agents = {
        "coordinator": (_TEMPLATES / "new-mas-coordinator.yaml").read_text(encoding="utf-8"),
        "researcher": (_TEMPLATES / "new-mas-researcher.yaml").read_text(encoding="utf-8"),
    }
    resp = client.post(
        "/api/libraries/demo/apps",
        json={"mas_name": "newmas", "mas_yaml": mas_yaml, "agents": agents},
    )
    assert resp.status_code == 201
    assert (demo_lab / "apps" / "newmas" / "mas.yaml").exists()

    updated_mas_yaml = mas_yaml.replace("name: newmas", "name: renamed-mas")
    resp = client.put(
        "/api/libraries/demo/apps/newmas",
        json={"mas_name": "renamed-mas", "mas_yaml": updated_mas_yaml, "agents": agents},
    )
    assert resp.status_code == 200
    assert (demo_lab / "apps" / "renamed-mas" / "mas.yaml").exists()
    assert not (demo_lab / "apps" / "newmas").exists()

    client.delete("/api/libraries/demo/apps/renamed-mas")


def test_mas_canvas_persists_to_ui_canvas_overlay(client, demo_lab):
    mas_yaml = "\n".join(
        [
            "apiVersion: mas/v1",
            "kind: MAS",
            "metadata:",
            "  name: canvas-app",
            "spec:",
            "  agency:",
            "    agents:",
            "      - id: coordinator",
            "        ref: agents/coordinator.yaml",
            "  workflow:",
            "    entry: coordinator",
            "    nodes:",
            "      - id: coordinator",
            "        role: specialist",
            "x-canvas-positions:",
            "  coordinator: {x: 10, y: 20}",
            "",
        ]
    )
    agents = {
        "coordinator": "\n".join(
            [
                "apiVersion: mas/v1",
                "kind: Agent",
                "metadata:",
                "  name: coordinator",
                "  x-node-id: node_coord",
                "spec:",
                "  x-text-input: hello",
                "",
            ]
        )
    }
    resp = client.post(
        "/api/libraries/demo/apps",
        json={"mas_name": "canvas-app", "mas_yaml": mas_yaml, "agents": agents},
    )
    assert resp.status_code == 201
    app_dir = demo_lab / "apps" / "canvas-app"
    on_disk = (app_dir / "mas.yaml").read_text(encoding="utf-8")
    assert "x-canvas-positions" not in on_disk
    overlay_path = app_dir / "overlays" / "ui-canvas.yaml"
    assert overlay_path.is_file()
    overlay_text = overlay_path.read_text(encoding="utf-8")
    assert "x-ui-canvas: true" in overlay_text
    assert "x-canvas-positions" in overlay_text

    listed = client.get("/api/libraries/demo/overlays").json()["overlays"]
    canvas_rows = [o for o in listed if o["name"] == "ui-canvas"]
    assert canvas_rows
    assert canvas_rows[0]["ui_canvas"] is True

    detail = client.get("/api/libraries/demo/apps/canvas-app").json()
    assert "x-canvas-positions" in detail["mas_yaml"]
    assert detail["ui_canvas_overlay"].endswith("overlays/ui-canvas.yaml")
    assert "hello" in detail["agents"]["coordinator"]

    # Unparseable agent YAML must not drop the agent on save.
    resp = client.put(
        "/api/libraries/demo/apps/canvas-app",
        json={
            "mas_name": "canvas-app",
            "mas_yaml": mas_yaml,
            "agents": {"coordinator": "spec: [\n"},
        },
    )
    assert resp.status_code == 200
    saved_agent = (app_dir / "agents" / "coordinator.yaml").read_text(encoding="utf-8")
    assert saved_agent == "spec: [\n"



# -- Pipeline step types ------------------------------------------------------

def test_pipeline_step_types(client):
    data = client.get("/api/pipeline-step-types").json()
    assert "step_types" in data
    assert len(data["step_types"]) > 0


# -- Metrics ------------------------------------------------------------------

def test_metrics_endpoints(client):
    assert client.get("/api/metrics/eval").status_code == 200
    assert client.get("/api/metrics/mce").status_code == 200


# -- Jobs ---------------------------------------------------------------------

def test_jobs_list_empty(client):
    from mas.lab.controller import api as api_mod
    from mas.lab.controller import jobs

    jobs._jobs.clear()
    api_mod._INSTANCE = None
    assert client.get("/api/jobs").json()["jobs"] == []


def test_experiment_run_starts_controller_worker(client, monkeypatch):
    submitted: dict = {}

    class _FakeAPI:
        def submit_experiment(self, spec):
            submitted.update(spec)
            return {"worker_id": "w-exp-1", "job_id": "w-exp-1", "status": "pending"}

    monkeypatch.setattr(
        "mas.lab.controller.routes.experiments.get_controller_api",
        lambda workspace=None: _FakeAPI(),
    )
    resp = client.post(
        "/api/libraries/demo/experiment/run",
        json={
            "experiment_yaml": "trip-planner-design-pattern-experiment.yaml",
            "query": "hello",
            "demo_ui_ingest_url": "http://127.0.0.1:8765/api/ingest",
        },
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["job_id"] == "w-exp-1"
    assert submitted["progress"] is False
    assert submitted["live"] is True
    assert submitted["experiment_yaml"].endswith("trip-planner-design-pattern-experiment.yaml")


def test_jobs_origin_filter_and_delete(client):
    from mas.lab.controller import api as api_mod
    from mas.lab.controller import jobs
    from mas.lab.controller.jobs import Job, JobStatus, now_iso

    jobs._jobs.clear()
    api_mod._INSTANCE = None
    ui = Job(
        id="ui-1",
        endpoint="/x",
        command="x",
        status=JobStatus.FAILED,
        created_at=now_iso(),
        request_body={"demo_ui_ref": {"origin": "demo-ui"}},
    )
    other = Job(
        id="cli-1",
        endpoint="/x",
        command="x",
        status=JobStatus.FAILED,
        created_at=now_iso(),
    )
    jobs._jobs[ui.id] = ui
    jobs._jobs[other.id] = other
    listed = client.get("/api/jobs", params={"origin": "demo-ui"}).json()["jobs"]
    assert [item["id"] for item in listed] == ["ui-1"]
    assert client.delete("/api/jobs", params={"origin": "demo-ui"}).json()["removed"] >= 1
    assert "ui-1" not in jobs._jobs
    assert "cli-1" in jobs._jobs
    assert client.delete("/api/jobs/cli-1").status_code == 200
    assert "cli-1" not in jobs._jobs


# -- Config files -------------------------------------------------------------

def test_config_files(client):
    data = client.get("/api/libraries/demo/config-files").json()
    assert "infra" in data
    infra_files = data["infra"]
    assert any("tool-providers" in k for k in infra_files)
    assert "flavours" in data
