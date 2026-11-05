#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Session lifecycle tests — acquire/release and idle shutdown."""

from __future__ import annotations

import mas.lab.controller.daemon as daemon_mod
from mas.lab.controller.api import ControllerAPI
from mas.lab.controller.sessions import SessionRegistry


def test_session_registry_idle_after_release():
    api = ControllerAPI()
    reg = SessionRegistry()
    reg.acquire("s1")
    assert reg.should_shutdown(api, idle_sec=0.0) is False
    reg.release("s1")
    assert reg.should_shutdown(api, idle_sec=0.0) is True


def test_acquire_release_rpc(temp_mas_home, monkeypatch):
    api = ControllerAPI()
    daemon_mod._api = api
    daemon_mod._sessions = SessionRegistry()
    daemon_mod._shutdown.clear()

    resp = daemon_mod._handle_request({"method": "acquire_session", "params": {"session_id": "test-cli-1"}})
    assert resp["result"]["ok"] is True
    assert daemon_mod._sessions.count() == 1

    status = daemon_mod._handle_request({"method": "status", "params": {}})["result"]
    assert status["sessions"]["sessions"] == 1

    daemon_mod._handle_request({"method": "release_session", "params": {"session_id": "test-cli-1"}})
    assert daemon_mod._sessions.count() == 0


def test_ensure_running_registers_session(temp_mas_home, monkeypatch):
    import mas.lab.controller.client as client_mod

    calls: list[str] = []

    def fake_call(self, method, params=None, timeout=30.0):
        calls.append(method)
        if method == "ping":
            return {"status": "ok"}
        if method == "acquire_session":
            return {"ok": True}
        return {}

    monkeypatch.setattr(client_mod.ControllerClient, "call", fake_call)
    monkeypatch.setattr(client_mod.ControllerClient, "is_running", lambda self: True)

    client = client_mod.ControllerClient()
    client.ensure_running(auto_start=False)
    assert "acquire_session" in calls
