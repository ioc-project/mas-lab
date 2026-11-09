#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Daemon IPC and lifecycle tests."""

from __future__ import annotations

import json
import threading
import time

import mas.lab.controller.daemon as daemon_mod
from mas.lab.controller import config as cfg
from mas.lab.controller.api import ControllerAPI
from mas.lab.controller.daemon import _handle_request, _serve_socket, main


def test_handle_request_all_methods(temp_mas_home, sample_lab, monkeypatch):
    api = ControllerAPI()
    monkeypatch.setattr(api.manifests, "refresh", lambda: None)
    api.manifests._libraries = {"demo": sample_lab}
    daemon_mod._api = api
    monkeypatch.setattr(daemon_mod, "_code_fingerprint", "abc")
    monkeypatch.setattr(daemon_mod, "_env_fingerprint", {"MAS_INFRA_REFS": "h"})

    assert _handle_request({"method": "ping"})["result"] == {
        "status": "ok",
        "code": "abc",
        "env": {"MAS_INFRA_REFS": "h"},
    }
    assert _handle_request({"method": "status"})["result"]["workers"] == 0
    assert "error" in _handle_request({"method": "unknown"})

    wid = _handle_request(
        {"method": "submit_benchmark", "params": {"experiment_yaml": str(sample_lab / "experiments" / "smoke.yaml")}}
    )["result"]["worker_id"]
    workers = _handle_request({"method": "list_workers", "params": {}})["result"]
    assert any(w["id"] == wid for w in workers)
    detail = _handle_request({"method": "get_worker", "params": {"worker_id": wid}})["result"]
    assert detail["id"] == wid
    _handle_request({"method": "cancel_worker", "params": {"worker_id": wid}})
    libs = _handle_request({"method": "list_libraries", "params": {}})["result"]
    assert isinstance(libs, list)


def test_serve_socket_once(temp_mas_home, monkeypatch):
    daemon_mod._api = ControllerAPI()
    daemon_mod._shutdown.clear()

    def stop_soon():
        time.sleep(0.3)
        daemon_mod._shutdown.set()

    threading.Thread(target=stop_soon, daemon=True).start()

    def run_client():
        time.sleep(0.1)
        import socket

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(str(cfg.socket_path()))
        sock.sendall(json.dumps({"method": "ping", "params": {}}).encode() + b"\n")
        data = sock.recv(4096)
        sock.close()
        assert b"ok" in data

    client = threading.Thread(target=run_client, daemon=True)
    client.start()
    _serve_socket(cfg.socket_path())
    client.join(timeout=2)


def test_main_no_http(temp_mas_home, monkeypatch):
    monkeypatch.setattr(daemon_mod, "_serve_socket", lambda _p: daemon_mod._shutdown.set())
    monkeypatch.setattr(daemon_mod, "_serve_http", lambda **kw: None)
    code = main(["--port", "9010", "--no-http"])
    assert code == 0


def test_handle_request_exception(temp_mas_home, monkeypatch):
    api = ControllerAPI()
    daemon_mod._api = api
    monkeypatch.setattr(api, "get_worker", lambda _id: (_ for _ in ()).throw(ValueError("bad")))
    resp = _handle_request({"method": "get_worker", "params": {"worker_id": "x"}})
    assert "error" in resp
