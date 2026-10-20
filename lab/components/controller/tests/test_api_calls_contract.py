#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Contract tests mirroring mas-lab-ui ``apiCalls.ts`` HTTP surface."""
from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import AsyncMock

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
    from mas.lab.controller import deps, fastapi_app, jobs
    from mas.lab.controller.manifest_store import ManifestStore

    store = ManifestStore(workspace=None)
    store._libraries = {"demo": demo_lab}
    store._registry._libraries = {"demo": demo_lab}

    monkeypatch.setattr(deps, "_manifest_store", None)
    monkeypatch.setattr("mas.lab.controller.deps.get_manifest_store", lambda: store)
    monkeypatch.setattr(
        "mas.lab.controller.lab_registry.LabRegistry.discover_bundled_infra",
        classmethod(lambda cls: {}),
    )
    monkeypatch.setattr(
        "mas.lab.controller.lab_registry.LabRegistry.discover_workspace_infra",
        classmethod(lambda cls, _ws: {}),
    )
    jobs._jobs.clear()
    return TestClient(fastapi_app.app)


@pytest.fixture
def mock_submit_job(monkeypatch):
    from mas.lab.controller import jobs

    def fake_submit(endpoint, cmd, cwd, timeout=60, env_override=None, request_body=None, cleanup_paths=None):
        job = jobs.Job(
            id="job-contract-1",
            endpoint=endpoint,
            command=" ".join(cmd),
            status=jobs.JobStatus.COMPLETED,
            created_at=jobs.now_iso(),
            finished_at=jobs.now_iso(),
            exit_code=0,
            stdout="job output",
        )
        jobs._jobs[job.id] = job
        return job

    def fake_chat_submit(
        endpoint,
        manifest_yaml,
        query,
        lib_dir,
        flavour=None,
        session_id=None,
        timeout=60,
        request_body=None,
    ):
        job = jobs.Job(
            id="job-chat-contract-1",
            endpoint=endpoint,
            command=f"agent-chat({lib_dir.name})",
            status=jobs.JobStatus.COMPLETED,
            created_at=jobs.now_iso(),
            finished_at=jobs.now_iso(),
            exit_code=0,
            response="Hello from agent",
            session_id=session_id or "ui:test",
        )
        jobs._jobs[job.id] = job
        return job

    monkeypatch.setattr("mas.lab.controller.jobs.submit_job", fake_submit)
    monkeypatch.setattr("mas.lab.controller.jobs.submit_agent_chat_job", fake_chat_submit)
    return fake_submit


@pytest.fixture
def mock_overlay_validate(monkeypatch):
    monkeypatch.setattr(
        "mas.lab.controller.deps.validate_overlay_content",
        AsyncMock(return_value=None),
    )



# --- Libraries (apiCalls: fetchLibraries, tools, skills, config-files) ---


def test_contract_libraries_list(client):
    data = client.get("/api/libraries").json()
    assert "libraries" in data
    assert data["libraries"][0]["dir"] == "demo"


def test_contract_tools_skills_config(client):
    tools = client.get("/api/libraries/demo/tools").json()
    assert "tools" in tools
    tool_names = {t["name"] for t in tools["tools"]}
    assert {"global/calc", "global/web-search"}.issubset(tool_names)

    skills = client.get("/api/libraries/demo/skills").json()
    assert "skills" in skills
    skill_names = {s["name"] for s in skills["skills"]}
    assert "global/answer-formatting" in skill_names

    cfg = client.get("/api/libraries/demo/config-files").json()
    assert "infra" in cfg
    assert any("tool-providers" in k for k in cfg["infra"])


# --- Validate (apiCalls: validateManifest) ---


def test_contract_validate_agent_manifest(client):
    agent_yaml = (_TEMPLATES / "validate-agent.yaml").read_text(encoding="utf-8")
    resp = client.post(
        "/api/libraries/demo/validate",
        json={"manifest_yaml": agent_yaml},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["command"] == "validate_data"
    assert body["exit_code"] == 1
    assert body["valid"] is False


def test_contract_validate_mas_manifest(client):
    mas_yaml = (_TEMPLATES / "validate-mas.yaml").read_text(encoding="utf-8")
    resp = client.post(
        "/api/libraries/demo/validate",
        json={"manifest_yaml": mas_yaml},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["command"] == "validate_data"
    assert "exit_code" in body


# --- Jobs (apiCalls: fetchJobs, fetchJobDetail, pollJob) ---


def test_contract_schemas_api(client):
    """apiCalls may migrate to GET /api/schemas/* instead of bundled copies."""
    listed = client.get("/api/schemas").json()
    assert "schemas" in listed
    ids = {s["id"] for s in listed["schemas"]}
    assert {"agent", "mas", "pipeline", "overlay", "infra"}.issubset(ids)

    agent = client.get("/api/schemas/agent")
    assert agent.status_code == 200
    assert "agent" in agent.text.lower() or "Agent" in agent.text


def test_contract_health(client):
    """OSS adds /api/health (UI may probe it for daemon readiness)."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("status") == "ok"


def test_contract_jobs(mock_submit_job, client):
    submit = client.post(
        "/api/libraries/demo/benchmark/run",
        json={"experiment_yaml": "metadata:\n  name: t\n", "progress": False},
    )
    assert submit.status_code == 202
    job_id = submit.json()["job_id"]

    listed = client.get("/api/jobs").json()
    assert any(j["id"] == job_id for j in listed["jobs"])

    # apiCalls: fetchJobs(status?) — optional status query param
    filtered = client.get("/api/jobs", params={"status": "completed"}).json()
    assert all(j["status"] == "completed" for j in filtered["jobs"])

    detail = client.get(f"/api/jobs/{job_id}").json()
    assert detail["id"] == job_id
    assert "stdout" in detail


# --- Run agent / MAS (apiCalls: runAgent, runMas) ---


def test_contract_run_agent_and_mas(mock_submit_job, client):
    agent_yaml = (_TEMPLATES / "validate-agent.yaml").read_text(encoding="utf-8")
    agent = client.post(
        "/api/libraries/demo/run",
        json={"manifest_yaml": agent_yaml, "query": "hi", "verbose": False},
    )
    assert agent.status_code == 202
    body = agent.json()
    # UI RunAgentSubmitResponse fields
    assert "job_id" in body
    assert "status" in body
    assert "command" in body
    assert "session_id" in body

    mas_yaml = (_TEMPLATES / "validate-mas.yaml").read_text(encoding="utf-8")
    mas = client.post(
        "/api/libraries/demo/run-mas",
        json={"manifest_yaml": mas_yaml, "query": "hi", "overlays": []},
    )
    assert mas.status_code == 202
    mas_body = mas.json()
    # UI JobSubmitResponse fields
    assert "job_id" in mas_body
    assert "status" in mas_body
    assert "command" in mas_body


# --- Benchmark (apiCalls: runBenchmark) ---


def test_contract_benchmark_run(mock_submit_job, client):
    exp_yaml = (_TEMPLATES / "new-experiment.yaml").read_text(encoding="utf-8")
    resp = client.post(
        "/api/libraries/demo/benchmark/run",
        json={"experiment_yaml": exp_yaml, "progress": True},
    )
    assert resp.status_code == 202
    body = resp.json()
    # UI BenchmarkRunSubmitResponse fields
    assert "job_id" in body
    assert "status" in body
    assert "command" in body

    # apiCalls sends max_runs; accept legacy n_runs alias too
    with_runs = client.post(
        "/api/libraries/demo/benchmark/run",
        json={"experiment_yaml": exp_yaml, "n_runs": 3},
    )
    assert with_runs.status_code == 202
    runs_body = with_runs.json()
    assert "job_id" in runs_body
    assert "--max-runs 3" in runs_body["command"]


# --- MAS resources CRUD (apiCalls: fetch/create/update/deleteMasResource) ---


def test_contract_mas_resources_crud(client, demo_lab):
    listed = client.get("/api/libraries/demo/apps").json()
    assert "mas_resources" in listed
    assert "trip-planner" in listed["mas_resources"]

    detail = client.get("/api/libraries/demo/apps/trip-planner").json()
    assert detail["mas_name"] == "trip-planner"

    mas_yaml = (_TEMPLATES / "new-mas.yaml").read_text(encoding="utf-8")
    agents = {
        "coordinator": (_TEMPLATES / "new-mas-coordinator.yaml").read_text(encoding="utf-8"),
        "researcher": (_TEMPLATES / "new-mas-researcher.yaml").read_text(encoding="utf-8"),
    }

    created = client.post(
        "/api/libraries/demo/apps",
        json={"mas_name": "newmas", "mas_yaml": mas_yaml, "agents": agents},
    )
    assert created.status_code == 201
    create_body = created.json()
    assert create_body["mas_name"] == "newmas"
    assert "mas_file" in create_body
    assert "agents" in create_body

    updated_yaml = mas_yaml.replace("name: newmas", "name: newmas-renamed")
    updated = client.put(
        "/api/libraries/demo/apps/newmas",
        json={"mas_name": "newmas-renamed", "mas_yaml": updated_yaml, "agents": agents},
    )
    assert updated.status_code == 200
    update_body = updated.json()
    assert update_body["mas_name"] == "newmas-renamed"
    assert "mas_file" in update_body
    assert "agents" in update_body

    deleted = client.delete("/api/libraries/demo/apps/newmas-renamed")
    assert deleted.status_code in (200, 204)


# --- Scenarios / datasets list (apiCalls: fetchScenarios, fetchDatasets) ---


def test_contract_scenarios_and_datasets(client):
    scenarios = client.get("/api/libraries/demo/scenarios").json()
    assert "scenarios" in scenarios
    names = [s["name"] for s in scenarios["scenarios"]]
    assert "trip-planner" in names

    datasets = client.get("/api/libraries/demo/datasets").json()
    assert "datasets" in datasets
    ds_names = {d["name"] for d in datasets["datasets"]}
    assert {"benchmark.yaml", "qa-agent-default.yaml"}.issubset(ds_names)
    for d in datasets["datasets"]:
        assert "name" in d
        assert "path" in d


# --- Datasets CRUD (apiCalls: create/update/delete dataset) ---


def test_contract_datasets_crud(client, demo_lab):
    dataset_yaml = (
        "apiVersion: lab/v1\nkind: Dataset\nmetadata:\n  name: new-dataset\n"
        "spec:\n  items: []\n"
    )
    created = client.post(
        "/api/libraries/demo/datasets",
        json={"name": "new-dataset", "content": dataset_yaml},
    )
    assert created.status_code == 201
    assert (demo_lab / "datasets" / "new-dataset.yaml").exists()

    after_create = client.get("/api/libraries/demo/datasets").json()
    assert "new-dataset.yaml" in {d["name"] for d in after_create["datasets"]}

    renamed_yaml = dataset_yaml.replace("name: new-dataset", "name: renamed-dataset")
    updated = client.put(
        "/api/libraries/demo/datasets/new-dataset",
        json={"name": "renamed-dataset", "content": renamed_yaml},
    )
    assert updated.status_code == 200
    assert not (demo_lab / "datasets" / "new-dataset.yaml").exists()
    assert (demo_lab / "datasets" / "renamed-dataset.yaml").exists()

    after_rename = client.get("/api/libraries/demo/datasets").json()
    after_rename_names = {d["name"] for d in after_rename["datasets"]}
    assert "renamed-dataset.yaml" in after_rename_names
    assert "new-dataset.yaml" not in after_rename_names

    deleted = client.delete("/api/libraries/demo/datasets/renamed-dataset")
    assert deleted.status_code in (200, 204)
    assert not (demo_lab / "datasets" / "renamed-dataset.yaml").exists()

    after_delete = client.get("/api/libraries/demo/datasets").json()
    assert "renamed-dataset.yaml" not in {d["name"] for d in after_delete["datasets"]}


# --- Overlays CRUD (apiCalls: create/update/delete overlay) ---


def test_contract_overlays_crud(client, demo_lab):
    overlay_yaml = (
        "apiVersion: mas/v1\nkind: Overlay\nmetadata:\n  name: new-overlay\n"
        "  namespace: global\n  description: new overlay\n"
    )
    created = client.post(
        "/api/libraries/demo/overlays",
        json={"name": "new-overlay", "content": overlay_yaml, "run_validation": False},
    )
    assert created.status_code == 201
    assert (demo_lab / "overlays" / "new-overlay.yaml").exists()

    after_create = client.get("/api/libraries/demo/overlays").json()
    assert "new-overlay" in {o["name"] for o in after_create["overlays"]}

    renamed_yaml = overlay_yaml.replace("name: new-overlay", "name: renamed-overlay")
    updated = client.put(
        "/api/libraries/demo/overlays/new-overlay",
        json={"name": "renamed-overlay", "content": renamed_yaml, "run_validation": False},
    )
    assert updated.status_code == 200
    assert not (demo_lab / "overlays" / "new-overlay.yaml").exists()
    assert (demo_lab / "overlays" / "renamed-overlay.yaml").exists()

    after_rename = client.get("/api/libraries/demo/overlays").json()
    after_rename_names = {o["name"] for o in after_rename["overlays"]}
    assert "renamed-overlay" in after_rename_names
    assert "new-overlay" not in after_rename_names

    deleted = client.delete("/api/libraries/demo/overlays/renamed-overlay")
    assert deleted.status_code in (200, 204)
    assert not (demo_lab / "overlays" / "renamed-overlay.yaml").exists()

    after_delete = client.get("/api/libraries/demo/overlays").json()
    assert "renamed-overlay" not in {o["name"] for o in after_delete["overlays"]}


# --- Experiments CRUD (apiCalls: fetch/create/update/delete experiment) ---


def test_contract_experiments_crud(client, demo_lab):
    listed = client.get("/api/libraries/demo/experiments").json()
    assert "experiments" in listed
    exp_names = {e["name"] for e in listed["experiments"]}
    assert "trip-planner-design-pattern-experiment" in exp_names

    content = client.get(
        "/api/libraries/demo/experiments/trip-planner-design-pattern-experiment"
    ).json()
    assert "content" in content

    exp_yaml = (_TEMPLATES / "new-experiment.yaml").read_text(encoding="utf-8")
    created = client.post(
        "/api/libraries/demo/experiments",
        json={"name": "new-experiment", "content": exp_yaml},
    )
    assert created.status_code == 201
    assert (demo_lab / "experiments" / "new-experiment.yaml").exists()

    # Regression guard: the manifest-file cache must be invalidated on write,
    # otherwise this list would still be the one scanned before the create.
    after_create = client.get("/api/libraries/demo/experiments").json()
    assert "new-experiment" in {e["name"] for e in after_create["experiments"]}

    readback = client.get("/api/libraries/demo/experiments/new-experiment").json()
    assert "content" in readback
    import yaml as _yaml
    parsed = _yaml.safe_load(readback["content"])
    from mas.ctl.validate.validator import validate_data
    result = validate_data(parsed, kind="experiment", strict=True, resolve_refs=False)
    assert result.ok, f"schema validation failed: {[i.message for i in result.issues]}"

    updated_yaml = exp_yaml.replace(
        "name: new-experiment", "name: renamed-experiment"
    )
    updated = client.put(
        "/api/libraries/demo/experiments/new-experiment",
        json={"name": "renamed-experiment", "content": updated_yaml},
    )
    assert updated.status_code == 200
    assert not (demo_lab / "experiments" / "new-experiment.yaml").exists()
    assert (demo_lab / "experiments" / "renamed-experiment.yaml").exists()

    after_rename = client.get("/api/libraries/demo/experiments").json()
    after_rename_names = {e["name"] for e in after_rename["experiments"]}
    assert "renamed-experiment" in after_rename_names
    assert "new-experiment" not in after_rename_names

    deleted = client.delete("/api/libraries/demo/experiments/renamed-experiment")
    assert deleted.status_code in (200, 204)
    assert not (demo_lab / "experiments" / "renamed-experiment.yaml").exists()

    after_delete = client.get("/api/libraries/demo/experiments").json()
    assert "renamed-experiment" not in {e["name"] for e in after_delete["experiments"]}


# --- Experiment cache / detail (apiCalls: deleteExperimentCache, fetchExperimentDetail) ---


def test_contract_experiment_cache_and_detail(client, tmp_path, monkeypatch):
    lab_root = tmp_path / "mas-lab-root"
    exp_dir = lab_root / "labs" / "cached-exp"
    exp_dir.mkdir(parents=True)
    (exp_dir / "metadata.yaml").write_text("name: cached-exp\nstatus: done\n", encoding="utf-8")
    (exp_dir / "results.txt").write_text("ok\n", encoding="utf-8")
    monkeypatch.setattr("mas.lab.controller.routes.benchmark.MAS_LAB_ROOT", lab_root)

    listed = client.get("/api/experiments").json()
    assert any(e["name"] == "cached-exp" for e in listed["experiments"])

    detail = client.get("/api/experiments/cached-exp")
    assert detail.status_code == 200
    body = detail.json()
    assert body["name"] == "cached-exp"
    assert "tree" in body

    file_resp = client.get("/api/experiments/cached-exp/file", params={"path": "results.txt"})
    assert file_resp.status_code == 200
    assert "content" in file_resp.json()

    cache_del = client.delete("/api/experiments/cached-exp")
    assert cache_del.status_code == 200
    assert not exp_dir.exists()


# --- Pipeline step types (apiCalls: fetchPipelineStepTypes) ---


def test_contract_pipeline_step_types(client):
    data = client.get("/api/pipeline-step-types").json()
    assert "step_types" in data
    assert "categories" in data


# --- Pipelines CRUD + validate + run (apiCalls) ---


def test_contract_pipelines(mock_submit_job, client, demo_lab):
    listed = client.get("/api/libraries/demo/pipelines").json()
    assert "pipelines" in listed
    pipe_names = {p["name"] for p in listed["pipelines"]}
    assert "pipeline-test" in pipe_names

    detail = client.get("/api/libraries/demo/pipelines/pipeline-test").json()
    assert "content" in detail

    pipeline_yaml = (_TEMPLATES / "validate-pipeline.yaml").read_text(encoding="utf-8")

    validate = client.post(
        "/api/libraries/demo/pipelines/validate",
        json={"manifest_yaml": pipeline_yaml},
    )
    assert validate.status_code == 200

    created = client.post(
        "/api/libraries/demo/pipelines",
        json={"name": "analysis-pipeline", "content": pipeline_yaml},
    )
    assert created.status_code == 201
    assert (demo_lab / "pipelines" / "analysis-pipeline.yaml").exists()

    after_create = client.get("/api/libraries/demo/pipelines").json()
    assert "analysis-pipeline" in {p["name"] for p in after_create["pipelines"]}

    renamed_yaml = pipeline_yaml.replace(
        "name: analysis-pipeline", "name: renamed-pipeline"
    )
    updated = client.put(
        "/api/libraries/demo/pipelines/analysis-pipeline",
        json={"name": "renamed-pipeline", "content": renamed_yaml},
    )
    assert updated.status_code == 200
    assert not (demo_lab / "pipelines" / "analysis-pipeline.yaml").exists()
    assert (demo_lab / "pipelines" / "renamed-pipeline.yaml").exists()

    after_rename = client.get("/api/libraries/demo/pipelines").json()
    after_rename_names = {p["name"] for p in after_rename["pipelines"]}
    assert "renamed-pipeline" in after_rename_names
    assert "analysis-pipeline" not in after_rename_names

    run = client.post(
        "/api/libraries/demo/pipeline/run",
        json={"pipeline_yaml": renamed_yaml},
    )
    assert run.status_code == 202
    assert "job_id" in run.json()

    # /pipeline/run writes an inline-content temp file (pipeline-*.yaml) directly
    # under the library root; mock_submit_job doesn't run the real cleanup, so
    # remove it here to isolate the delete-reflects-in-list assertion below
    # from this unrelated temp-file artifact (unrestricted pipeline discovery
    # scans the whole library tree, not just pipelines/).
    for tmp_file in demo_lab.glob("pipeline-*.yaml"):
        tmp_file.unlink()

    deleted = client.delete("/api/libraries/demo/pipelines/renamed-pipeline")
    assert deleted.status_code in (200, 204)
    assert not (demo_lab / "pipelines" / "renamed-pipeline.yaml").exists()

    after_delete = client.get("/api/libraries/demo/pipelines").json()
    assert "renamed-pipeline" not in {p["name"] for p in after_delete["pipelines"]}


# --- Overlays CRUD + validate (apiCalls) ---


def test_contract_overlays(mock_overlay_validate, client, demo_lab):
    listed = client.get("/api/libraries/demo/overlays").json()
    assert "overlays" in listed
    overlay_names = {o["name"] for o in listed["overlays"]}
    assert {"cot-moderator", "react-moderator", "reflection-moderator"}.issubset(overlay_names)

    detail = client.get("/api/libraries/demo/overlays/reflection-moderator").json()
    assert "content" in detail

    overlay_yaml = (_TEMPLATES / "validate-overlay.yaml").read_text(encoding="utf-8")

    validate = client.post(
        "/api/libraries/demo/overlays/validate",
        json={"manifest_yaml": overlay_yaml},
    )
    assert validate.status_code == 200

    created = client.post(
        "/api/libraries/demo/overlays",
        json={"name": "contract-overlay", "content": overlay_yaml, "run_validation": False},
    )
    assert created.status_code == 201

    renamed_yaml = overlay_yaml.replace(
        "name: verbose-moderator", "name: contract-overlay-renamed"
    )
    updated = client.put(
        "/api/libraries/demo/overlays/contract-overlay",
        json={"name": "contract-overlay-renamed", "content": renamed_yaml, "run_validation": False},
    )
    assert updated.status_code == 200
    assert not (demo_lab / "overlays" / "contract-overlay.yaml").exists()
    assert (demo_lab / "overlays" / "contract-overlay-renamed.yaml").exists()

    deleted = client.delete("/api/libraries/demo/overlays/contract-overlay-renamed")
    assert deleted.status_code in (200, 204)


# --- Datasets CRUD (apiCalls: fetchDatasetsList, create/update/delete) ---


def test_contract_datasets_crud(client, demo_lab):
    listed = client.get("/api/libraries/demo/datasets").json()
    assert "datasets" in listed

    detail = client.get("/api/libraries/demo/datasets/benchmark.yaml").json()
    assert "content" in detail

    created = client.post(
        "/api/libraries/demo/datasets",
        json={"name": "contract-ds.yaml", "content": "metadata:\n  name: contract-ds\nitems: []\n"},
    )
    assert created.status_code == 201

    updated = client.put(
        "/api/libraries/demo/datasets/contract-ds.yaml",
        json={"name": "contract-ds.yaml", "content": "metadata:\n  name: contract-ds\nitems: []\n"},
    )
    assert updated.status_code == 200

    deleted = client.delete("/api/libraries/demo/datasets/contract-ds.yaml")
    assert deleted.status_code in (200, 204)


# --- Metrics (apiCalls: useEvalMetrics, useMceMetrics) ---


def test_contract_metrics(client):
    eval_metrics = client.get("/api/metrics/eval").json()
    assert isinstance(eval_metrics, dict)
    mce_metrics = client.get("/api/metrics/mce").json()
    assert isinstance(mce_metrics, dict)


# --- Registry / discovery / info (daemon surface; UI may adopt) ---


def test_contract_registry_defaults_discovery_info(client, monkeypatch):
    from mas.lab.controller import lab_registry

    class _FakeRegistry:
        def catalog(self):
            return {
                "runtime": {},
                "pipeline_steps": {},
                "defaults": {"models": [{"model": "gpt-4o-mini"}]},
                "design_patterns": [],
            }

        def agent_defaults(self):
            return {"design_pattern": {"type": "react"}, "models": []}

        def default_model(self):
            return "gpt-4o-mini"

    monkeypatch.setattr(
        lab_registry,
        "get_lab_registry",
        lambda _ws=None: _FakeRegistry(),
    )

    registry = client.get("/api/registry").json()
    assert "registry" in registry

    defaults = client.get("/api/defaults").json()
    assert "agent" in defaults
    assert "default_model" in defaults

    discovery = client.get("/api/discovery").json()
    assert isinstance(discovery, dict)

    info = client.get("/api/info").json()
    assert isinstance(info, dict)


def test_contract_runtime_runners(client):
    from mas.lab.runners.constants import DEFAULT_LAB_RUNNER_ID

    resp = client.get("/api/runtime-runners")
    assert resp.status_code == 200
    body = resp.json()
    assert "runners" in body
    ids = {r["id"] for r in body["runners"]}
    assert DEFAULT_LAB_RUNNER_ID in ids


def test_contract_experiment_definitions(client):
    resp = client.get("/api/experiments/definitions")
    assert resp.status_code == 200
    body = resp.json()
    assert "experiments" in body
    assert isinstance(body["experiments"], list)


def test_contract_benchmark_analyze(mock_submit_job, client):
    analyze = client.post(
        "/api/libraries/demo/benchmark/analyze",
        json={"benchmark_id": "demo-run-1"},
    )
    assert analyze.status_code == 202
    assert "job_id" in analyze.json()


def test_contract_benchmark_export(mock_submit_job, client):
    export = client.post(
        "/api/libraries/demo/benchmark/export",
        json={"benchmark_id": "demo-run-1", "dry_run": True},
    )
    assert export.status_code == 202
    body = export.json()
    assert "job_id" in body
    assert "mas-lab" in body["command"]
    assert "export" in body["command"]


def test_contract_benchmark_import(mock_submit_job, client):
    imp = client.post(
        "/api/libraries/demo/benchmark/import",
        json={"tarball": "/tmp/demo-run.tar.gz", "dry_run": True},
    )
    assert imp.status_code == 202
    body = imp.json()
    assert "job_id" in body
    assert "import" in body["command"]


def test_contract_topologies(client, demo_lab):
    (demo_lab / "topologies").mkdir(exist_ok=True)
    (demo_lab / "topologies" / "linear.yaml").write_text("name: linear\n", encoding="utf-8")

    resp = client.get("/api/libraries/demo/topologies")
    assert resp.status_code == 200
    assert "linear.yaml" in resp.json()["topologies"]


def test_contract_multi_turn_and_eval_output(mock_submit_job, client):
    mt = client.post(
        "/api/libraries/demo/run/multi-turn",
        json={
            "manifest_yaml": "kind: Agent\n",
            "queries": ["q1", "q2"],
            "verbose": False,
        },
    )
    assert mt.status_code == 202
    assert "job_id" in mt.json()

    ev = client.post(
        "/api/libraries/demo/eval-output",
        json={"events_file": "events.jsonl", "metrics": ["answer_relevancy"]},
    )
    assert ev.status_code == 202
    assert "job_id" in ev.json()


def test_contract_job_cancel_and_clear(mock_submit_job, client):
    submit = client.post(
        "/api/libraries/demo/benchmark/run",
        json={"experiment_yaml": "metadata:\n  name: t\n", "progress": False},
    )
    job_id = submit.json()["job_id"]

    cancel = client.delete(f"/api/jobs/{job_id}")
    assert cancel.status_code == 200
    assert cancel.json().get("job_id") == job_id or "message" in cancel.json()

    cleared = client.delete("/api/jobs")
    assert cleared.status_code == 200
    assert "removed" in cleared.json()
