#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for ApplicationRunner registry and controller API."""
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_application_runner_registry_mas_lab():
    from mas.lab.runners.constants import DEFAULT_LAB_RUNNER_ID
    from mas.lab.runners.registry import ApplicationRunnerRegistry

    ApplicationRunnerRegistry.reset()
    available = ApplicationRunnerRegistry.available()
    assert DEFAULT_LAB_RUNNER_ID in available
    runner = ApplicationRunnerRegistry.get(DEFAULT_LAB_RUNNER_ID)
    assert runner.runner_id == DEFAULT_LAB_RUNNER_ID


def test_manifest_store_discovers_labs():
    from mas.lab.controller.manifest_store import ManifestStore

    with tempfile.TemporaryDirectory() as tmp:
        ws_root = Path(tmp)
        labs = ws_root / "labs"
        lab = labs / "demo.lab"
        (lab / "experiments").mkdir(parents=True)
        (lab / "experiments" / "smoke.yaml").write_text(
            "metadata:\n  name: smoke\n  description: test\n",
            encoding="utf-8",
        )
        store = ManifestStore(workspace=None)
        store._libraries = {"demo": lab}
        exps = store.list_yaml_resources("demo", "experiments")
        assert len(exps) == 1
        assert exps[0]["name"] == "smoke"


def test_controller_api_submit_benchmark_worker():
    from mas.lab.controller.api import ControllerAPI

    api = ControllerAPI()
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
        fh.write("metadata:\n  name: dry\n")
        exp_path = fh.name
    try:
        result = api.submit_benchmark(
            {"experiment_yaml": exp_path, "dry_run": True, "progress": False}
        )
        assert "worker_id" in result
        assert api.get_worker(result["worker_id"]) is not None
    finally:
        Path(exp_path).unlink(missing_ok=True)


def test_controller_api_submit_experiment_worker():
    from mas.lab.controller.api import ControllerAPI
    from mas.lab.controller.worker_model import WorkerKind

    api = ControllerAPI()
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
        fh.write("experiment:\n  name: live\n")
        exp_path = fh.name
    try:
        result = api.submit_experiment(
            {"experiment_yaml": exp_path, "progress": True, "demo_ui_ingest_url": "http://127.0.0.1:8765/api/ingest"}
        )
        assert "worker_id" in result
        detail = api.get_worker(result["worker_id"])
        assert detail is not None
        record = api.workers.get(result["worker_id"])
        assert record is not None
        assert record.kind is WorkerKind.EXPERIMENT
        assert record.spec.get("live") is True
        assert record.spec.get("progress") is False
    finally:
        Path(exp_path).unlink(missing_ok=True)


def test_get_manifest_store_refresh_throttled(monkeypatch):
    import mas.lab.controller.deps as deps

    fake_store = SimpleNamespace(refresh=lambda: None)
    calls = {"n": 0}

    def _refresh():
        calls["n"] += 1

    fake_store.refresh = _refresh

    monkeypatch.setattr(deps, "_manifest_store", fake_store)
    monkeypatch.setattr(deps, "_manifest_store_last_refresh", 100.0)
    monkeypatch.setattr(deps.time, "monotonic", lambda: 100.2)
    monkeypatch.setenv("MAS_CONTROLLER_REFRESH_INTERVAL_S", "1.0")

    deps.get_manifest_store()
    assert calls["n"] == 0


def test_get_manifest_store_refresh_forced_with_zero_interval(monkeypatch):
    import mas.lab.controller.deps as deps

    calls = {"n": 0}

    def _refresh():
        calls["n"] += 1

    fake_store = SimpleNamespace(refresh=_refresh)
    monkeypatch.setattr(deps, "_manifest_store", fake_store)
    monkeypatch.setattr(deps, "_manifest_store_last_refresh", 100.0)
    monkeypatch.setattr(deps.time, "monotonic", lambda: 100.1)
    monkeypatch.setenv("MAS_CONTROLLER_REFRESH_INTERVAL_S", "0")

    deps.get_manifest_store()
    assert calls["n"] == 1
