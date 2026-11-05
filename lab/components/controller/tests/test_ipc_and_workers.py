#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for IPC client, worker registry, and ControllerAPI."""

from __future__ import annotations

import threading
import time

from mas.lab.controller.api import ControllerAPI
from mas.lab.controller.registry import WorkerRegistry
from mas.lab.controller.worker_model import WorkerKind, WorkerStatus
from mas.lab.controller.workers import WorkerRunner


def test_worker_registry_lifecycle():
    reg = WorkerRegistry()
    record = reg.create(WorkerKind.APPLICATION, command="test", spec={"x": 1})
    assert reg.get(record.id) is record
    listed = reg.list_workers(kind=WorkerKind.APPLICATION)
    assert len(listed) == 1
    reg.cancel(record.id)
    assert reg.get(record.id).status == WorkerStatus.CANCELLED


def test_worker_runner_completes():
    reg = WorkerRegistry()
    runner = WorkerRunner(reg)
    record = reg.create(WorkerKind.APPLICATION, command="sync")
    done = threading.Event()

    def fn():
        time.sleep(0.05)
        done.set()
        return {"ok": True}

    runner.submit(record, fn)
    done.wait(timeout=2)
    for _ in range(50):
        if record.status == WorkerStatus.COMPLETED:
            break
        time.sleep(0.05)
    assert record.status == WorkerStatus.COMPLETED
    assert record.exit_code == 0


def test_controller_api_libraries(sample_lab, monkeypatch):
    api = ControllerAPI()
    monkeypatch.setattr(api.manifests, "refresh", lambda: None)
    api.manifests._libraries = {"demo": sample_lab}
    libs = api.list_libraries()
    assert any(lib["dir"] == "demo" for lib in libs)
    exps = api.list_experiments("demo")
    assert exps[0]["name"] == "smoke"


def test_application_worker():
    reg = WorkerRegistry()
    runner = WorkerRunner(reg)
    from mas.lab.controller.workers import run_application_worker

    record = run_application_worker(
        reg,
        runner,
        {
            "prompt": "hi",
            "spec_path": __import__("pathlib").Path(__file__),
            "config": {},
            "runner_id": "native",
        },
    )
    for _ in range(100):
        if record.status in (WorkerStatus.COMPLETED, WorkerStatus.FAILED):
            break
        time.sleep(0.1)
    assert record.status in (WorkerStatus.COMPLETED, WorkerStatus.FAILED)


def test_ipc_handle_request():
    import mas.lab.controller.daemon as daemon_mod
    from mas.lab.controller.daemon import _handle_request

    daemon_mod._api = ControllerAPI()
    assert _handle_request({"method": "ping", "params": {}})["result"]["status"] == "ok"
    status = _handle_request({"method": "status", "params": {}})["result"]
    assert status["status"] == "ok"
