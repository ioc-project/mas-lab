#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""CLI workers and UI runs share one catalog."""

from __future__ import annotations

import time
from pathlib import Path

from mas.lab.controller.api import ControllerAPI, set_controller_api
from mas.lab.controller.run_store import init_run_store
from mas.lab.controller.worker_model import WorkerKind, WorkerStatus
from scope_fixtures import build_store, copy_scope_fixtures, make_client


def _attach_api(experiment_yaml: Path) -> tuple[ControllerAPI, str]:
    api = ControllerAPI()
    rec = api.workers.create(
        WorkerKind.BENCHMARK,
        spec={"experiment_yaml": str(experiment_yaml)},
        command=f"mas-lab benchmark run {experiment_yaml}",
    )
    rec.status = WorkerStatus.RUNNING
    rec.started_at = time.time()
    rec.append_stdout("progress 1/3")
    set_controller_api(api)
    return api, rec.id


def test_list_runs_adopts_cli_worker(tmp_path: Path, monkeypatch):
    roots = copy_scope_fixtures(tmp_path)
    store = build_store(roots)
    http = make_client(store, monkeypatch)
    init_run_store(None)
    yaml_path = roots["lonely"] / "experiments" / "only.yaml"
    api, worker_id = _attach_api(yaml_path)
    try:
        listed = http.get("/api/runs").json()["runs"]
        match = next(row for row in listed if row["job_id"] == worker_id)
        assert match["kind"] == "experiment"
        assert match["item"] == "only"
        assert match["scope"] == "lab:lonely"
        assert match["status"] == "running"
        assert "progress 1/3" in (match.get("log") or "")
        assert match["request_summary"]["source"] == "cli"
        deleted = http.delete(f"/api/runs/{worker_id}")
        assert deleted.status_code == 200
        after = http.get("/api/runs").json()["runs"]
        assert all(row["job_id"] != worker_id for row in after)
    finally:
        set_controller_api(None)


def test_ui_run_submits_controller_worker(tmp_path: Path, monkeypatch):
    roots = copy_scope_fixtures(tmp_path)
    store = build_store(roots)
    http = make_client(store, monkeypatch)
    init_run_store(None)
    api = ControllerAPI()

    def fake_submit(spec):
        rec = api.workers.create(WorkerKind.BENCHMARK, spec=spec, command="mas-lab benchmark run")
        rec.status = WorkerStatus.COMPLETED
        rec.exit_code = 0
        rec.started_at = time.time()
        rec.finished_at = time.time()
        rec.append_stdout("Configuration valid")
        return {"worker_id": rec.id, "job_id": rec.id, "status": rec.status.value}

    monkeypatch.setattr(api, "submit_benchmark", fake_submit)
    monkeypatch.setattr("mas.lab.controller.routes.runs.peek_controller_api", lambda: api)
    set_controller_api(api)
    try:
        started = http.post(
            "/api/runs",
            json={"scope": "lab:lonely", "kind": "experiment", "item": "only"},
        )
        assert started.status_code == 202, started.text
        body = started.json()
        assert body["status"] == "completed"
        detail = http.get(f"/api/runs/{body['id']}").json()
        assert detail["job_id"] == body["job_id"]
        assert "Configuration valid" in (detail.get("log") or "")
        assert detail["request_summary"]["source"] == "ui"
        listed = http.get("/api/runs").json()["runs"]
        assert sum(1 for row in listed if row["job_id"] == body["job_id"]) == 1
    finally:
        set_controller_api(None)


def test_progress_line_from_run_info(tmp_path: Path):
    from mas.lab.controller.run_bridge import progress_line

    out = tmp_path / "output" / "a"
    out.mkdir(parents=True)
    (out / "run_info.json").write_text('{"status": "ok"}', encoding="utf-8")
    line = progress_line({"output_dir": str(tmp_path / "output")})
    assert line is not None
    assert "ok=1" in line
    assert "done=1" in line
