#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Additional coverage for controller infrastructure."""

from __future__ import annotations

import socket
import threading
import time

import pytest
from mas.lab.controller.api import ControllerAPI
from mas.lab.controller.client import ControllerClient
from mas.lab.controller.registry import WorkerRegistry
from mas.lab.controller.worker_model import WorkerKind, WorkerStatus
from mas.lab.controller.workers import WorkerRunner
from mas.lab.runners.protocol import ApplicationRunnerProtocol


def test_worker_cancel_mid_run():
    reg = WorkerRegistry()
    runner = WorkerRunner(reg)
    record = reg.create(WorkerKind.APPLICATION)

    def slow():
        time.sleep(1)

    runner.submit(record, slow)
    time.sleep(0.05)
    runner.cancel(record.id)
    time.sleep(0.15)
    assert record.status == WorkerStatus.CANCELLED


def test_worker_failure_path():
    reg = WorkerRegistry()
    runner = WorkerRunner(reg)
    record = reg.create(WorkerKind.APPLICATION)
    runner.submit(record, lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    time.sleep(0.2)
    assert record.status == WorkerStatus.FAILED
    assert record.error == "boom"


def test_registry_get_missing():
    reg = WorkerRegistry()
    assert reg.get("missing") is None
    assert reg.update("missing", status=WorkerStatus.FAILED) is None


def test_registry_cancel_completed():
    reg = WorkerRegistry()
    record = reg.create(WorkerKind.BENCHMARK)
    record.status = WorkerStatus.COMPLETED
    assert reg.cancel(record.id) is True


def test_api_list_pipelines_bad_yaml(sample_lab, monkeypatch):
    api = ControllerAPI()
    monkeypatch.setattr(api.manifests, "refresh", lambda: None)
    api.manifests._libraries = {"demo": sample_lab}
    (sample_lab / "pipelines" / "bad.yaml").write_text(": [invalid", encoding="utf-8")
    pipes = api.list_pipelines("demo")
    assert any(p["filename"] == "bad" for p in pipes)


def test_api_submit_application(sample_lab, monkeypatch):
    api = ControllerAPI()
    monkeypatch.setattr(api.manifests, "refresh", lambda: None)
    api.manifests._libraries = {"demo": sample_lab}
    result = api.submit_application(
        {
            "prompt": "test",
            "spec_path": str(sample_lab / "experiments" / "smoke.yaml"),
            "config": {},
        }
    )
    assert "worker_id" in result


def test_api_list_workers_filtered(sample_lab, monkeypatch):
    api = ControllerAPI()
    monkeypatch.setattr(api.manifests, "refresh", lambda: None)
    api.manifests._libraries = {"demo": sample_lab}
    api.submit_benchmark({"experiment_yaml": str(sample_lab / "experiments" / "smoke.yaml")})
    assert len(api.list_workers(kind="benchmark")) >= 1


def test_client_ensure_running_no_autostart(temp_mas_home):
    client = ControllerClient()
    with pytest.raises(RuntimeError, match="not running"):
        client.ensure_running(auto_start=False)


def test_client_wait_not_found(temp_mas_home, monkeypatch):
    import mas.lab.controller.client as client_mod

    class FakeClient:
        def ensure_running(self, **kw):
            return None

        def call(self, method, params=None, timeout=30.0):
            return None

    monkeypatch.setattr(client_mod, "ControllerClient", FakeClient)
    with pytest.raises(RuntimeError, match="not found"):
        client_mod.wait_for_worker("missing", timeout=1, poll=0.01)


def test_daemon_invalid_json(temp_mas_home, monkeypatch):
    import mas.lab.controller.daemon as daemon_mod
    from mas.lab.controller import config as cfg
    from mas.lab.controller.daemon import _serve_socket

    daemon_mod._api = ControllerAPI()
    daemon_mod._shutdown.clear()

    def stop():
        time.sleep(0.2)
        daemon_mod._shutdown.set()

    threading.Thread(target=stop, daemon=True).start()

    def bad_client():
        time.sleep(0.05)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(str(cfg.socket_path()))
        s.sendall(b"not-json\n")
        s.close()

    threading.Thread(target=bad_client, daemon=True).start()
    _serve_socket(cfg.socket_path())


def test_api_overlays_and_datasets(sample_lab, monkeypatch):
    api = ControllerAPI()
    monkeypatch.setattr(api.manifests, "refresh", lambda: None)
    api.manifests._libraries = {"demo": sample_lab}
    (sample_lab / "overlays" / "bad.yaml").write_text(": [", encoding="utf-8")
    overlays = api.list_overlays("demo")
    assert any(o["name"] == "bad" for o in overlays)
    datasets = api.list_datasets("demo")
    assert any(d["name"] == "items" for d in datasets)
    api.get_overlay_content("demo", "baseline")
    api.submit_pipeline({"pipeline_yaml": str(sample_lab / "pipelines" / "analysis.yaml"), "dry_run": True})


def test_api_get_worker_missing():
    api = ControllerAPI()
    assert api.get_worker("nope") is None
    assert api.cancel_worker("nope") is False


def test_manifest_discover_cwd_labs(tmp_path, monkeypatch):
    from mas.lab.controller.lab_registry import LabRegistry, reset_lab_registry

    monkeypatch.chdir(tmp_path)
    labs = tmp_path / "labs"
    lab = labs / "my.lab"
    lab.mkdir(parents=True)
    found = LabRegistry().library_paths()
    assert "my" in found
    reset_lab_registry()


def test_serve_http_mock(monkeypatch):
    import mas.lab.controller.daemon as daemon_mod

    called = {}

    def fake_uvicorn_run(app, **kw):
        called["port"] = kw.get("port")

    monkeypatch.setattr("uvicorn.run", fake_uvicorn_run)
    daemon_mod._serve_http(host="127.0.0.1", port=9099)
    assert called["port"] == 9099


def test_runner_get_supported_contracts():
    from mas.lab.runners.constants import DEFAULT_LAB_RUNNER_ID
    from mas.lab.runners.registry import ApplicationRunnerRegistry

    runner = ApplicationRunnerRegistry.get(DEFAULT_LAB_RUNNER_ID)
    assert "memory" in runner.get_supported_contracts()


class _MinimalRunner(ApplicationRunnerProtocol):
    runner_id = "min"

    def run(self, *a, **k):
        raise NotImplementedError

    def supports_contract(self, contract_id: str) -> bool:
        return contract_id == "x"


def test_protocol_default_contracts():
    assert _MinimalRunner().get_supported_contracts() == []
