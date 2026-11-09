#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Extended client tests with mocked socket."""

from __future__ import annotations

import json

import pytest
from mas.lab.controller.client import ControllerClient, start_daemon, wait_for_worker


class _FakeSocket:
    def __init__(self, response: dict):
        self._response = json.dumps(response).encode() + b"\n"
        self._sent = False

    def connect(self, _path):
        return None

    def settimeout(self, _t):
        return None

    def sendall(self, _data):
        self._sent = True

    def recv(self, _n):
        if not self._sent:
            return b""
        self._sent = False
        return self._response

    def close(self):
        return None


def test_client_call_success(temp_mas_home, monkeypatch):
    monkeypatch.setattr(
        "mas.lab.controller.client.socket.socket",
        lambda *a, **k: _FakeSocket({"result": {"status": "ok"}}),
    )
    client = ControllerClient()
    monkeypatch.setattr(client, "is_running", lambda: True)
    assert client.call("ping")["status"] == "ok"


def test_client_call_error(temp_mas_home, monkeypatch):
    monkeypatch.setattr(
        "mas.lab.controller.client.socket.socket",
        lambda *a, **k: _FakeSocket({"error": "boom"}),
    )
    client = ControllerClient()
    with pytest.raises(RuntimeError, match="boom"):
        client.call("status")


def test_client_is_running_true(temp_mas_home, monkeypatch):
    from mas.lab.controller import config as cfg

    sock = cfg.socket_path()
    sock.parent.mkdir(parents=True, exist_ok=True)
    sock.touch()
    monkeypatch.setattr(
        "mas.lab.controller.client.socket.socket",
        lambda *a, **k: _FakeSocket({"result": {"status": "ok"}}),
    )
    client = ControllerClient()
    assert client.is_running() is True


def test_start_daemon_foreground(temp_mas_home, monkeypatch):
    import mas.lab.controller.client as client_mod

    monkeypatch.setattr(client_mod.subprocess, "run", lambda *a, **k: None)
    start_daemon(foreground=True)


def test_ensure_running_auto_start(temp_mas_home, monkeypatch):
    import mas.lab.controller.client as client_mod

    started = {"v": False}

    def fake_start(**kw):
        started["v"] = True

    monkeypatch.setattr(client_mod, "start_daemon", fake_start)

    def fake_call(self, method, params=None, timeout=30.0):
        if method == "acquire_session":
            return {"ok": True}
        return {"status": "ok"} if method == "ping" else {}

    monkeypatch.setattr(client_mod.ControllerClient, "call", fake_call)
    monkeypatch.setattr(
        client_mod.ControllerClient,
        "is_running",
        lambda self: started["v"],
    )
    client = client_mod.ControllerClient()
    client.ensure_running(auto_start=True)
    assert started["v"]


def test_wait_for_worker_completes(temp_mas_home, monkeypatch):
    calls = {"n": 0}

    def fake_call(self, method, params=None, timeout=30.0):
        if method == "acquire_session":
            return {"ok": True}
        if method == "ping":
            return {"status": "ok"}
        calls["n"] += 1
        if calls["n"] > 1:
            return {"status": "completed", "exit_code": 0}
        return {"status": "running"}

    client = ControllerClient()
    monkeypatch.setattr(client, "ensure_running", lambda **kw: None)
    monkeypatch.setattr(client, "call", fake_call)
    monkeypatch.setattr("mas.lab.controller.client.ControllerClient", lambda: client)
    detail = wait_for_worker("w-1", timeout=5, poll=0.01)
    assert detail["status"] == "completed"


def test_client_is_running_false(temp_mas_home):
    client = ControllerClient()
    assert client.is_running() is False


def test_client_call_empty_response(temp_mas_home, monkeypatch):
    class _EmptySocket:
        def connect(self, _p):
            return None

        def settimeout(self, _t):
            return None

        def sendall(self, _d):
            return None

        def recv(self, _n):
            return b""

        def close(self):
            return None

    monkeypatch.setattr("mas.lab.controller.client.socket.socket", lambda *a, **k: _EmptySocket())
    client = ControllerClient()
    with pytest.raises(json.JSONDecodeError):
        client.call("ping")


def test_stop_daemon_not_running(temp_mas_home):
    import mas.lab.controller.client as client_mod

    assert client_mod.stop_daemon() is True


def test_stop_daemon_running(temp_mas_home, monkeypatch):
    import mas.lab.controller.client as client_mod
    from mas.lab.controller import config as cfg

    sock = cfg.socket_path()
    sock.parent.mkdir(parents=True, exist_ok=True)
    sock.touch()
    monkeypatch.setattr(
        client_mod.ControllerClient,
        "is_running",
        lambda self: True,
    )
    monkeypatch.setattr(client_mod.ControllerClient, "call", lambda self, *a, **k: None)
    assert client_mod.stop_daemon() is True


def test_start_daemon_detach(temp_mas_home, monkeypatch):
    import mas.lab.controller.client as client_mod

    called = {}

    def fake_popen(cmd, **kw):
        called["cmd"] = cmd
        return None

    monkeypatch.setattr(client_mod.subprocess, "Popen", fake_popen)
    start_daemon(detach=True)
    assert "mas.lab.controller.daemon" in called["cmd"]


def test_ensure_running_failure(temp_mas_home, monkeypatch):
    import mas.lab.controller.client as client_mod

    monkeypatch.setattr(client_mod, "start_daemon", lambda **kw: None)
    monkeypatch.setattr(client_mod.ControllerClient, "is_running", lambda self: False)
    monkeypatch.setattr(client_mod.time, "sleep", lambda _s: None)
    client = ControllerClient()
    with pytest.raises(RuntimeError, match="failed to start"):
        client.ensure_running(auto_start=True)


def _fake_daemon(monkeypatch, *, code, running=0, env=None):
    import mas.lab.controller.client as client_mod

    state = {"alive": True, "started": False, "stopped": False, "code": code, "env": env or {}}

    def fake_call(self, method, params=None, timeout=30.0):
        if method == "ping":
            return {"status": "ok", "code": state["code"], "env": state["env"]}
        if method == "status":
            return {"running": running}
        return {"ok": True}

    def fake_stop():
        state["stopped"] = True
        state["alive"] = False
        return True

    def fake_start(**kw):
        state["started"] = True
        state["alive"] = True
        state["code"] = "current"
        state["env"] = {}

    monkeypatch.setattr(client_mod.cfg, "code_fingerprint", lambda: "current")
    monkeypatch.setattr(client_mod.cfg, "env_fingerprint", lambda: {})
    monkeypatch.setattr(client_mod.ControllerClient, "call", fake_call)
    monkeypatch.setattr(client_mod.ControllerClient, "is_running", lambda self: state["alive"])
    monkeypatch.setattr(client_mod, "stop_daemon", fake_stop)
    monkeypatch.setattr(client_mod, "start_daemon", fake_start)
    return state


def test_ensure_running_restarts_idle_stale_daemon(temp_mas_home, monkeypatch):
    state = _fake_daemon(monkeypatch, code=None)
    ControllerClient().ensure_running(restart_stale=True)
    assert state["stopped"] and state["started"]


def test_ensure_running_keeps_current_daemon(temp_mas_home, monkeypatch):
    state = _fake_daemon(monkeypatch, code="current")
    ControllerClient().ensure_running(restart_stale=True)
    assert not state["stopped"] and not state["started"]


def test_ensure_running_refuses_busy_stale_daemon(temp_mas_home, monkeypatch):
    state = _fake_daemon(monkeypatch, code="old", running=1)
    with pytest.raises(RuntimeError, match="another mas-lab installation.*1 running worker"):
        ControllerClient().ensure_running(restart_stale=True)
    assert not state["stopped"]


def test_ensure_running_warns_on_stale_daemon_by_default(temp_mas_home, monkeypatch, caplog):
    state = _fake_daemon(monkeypatch, code="old")
    with caplog.at_level("WARNING", logger="mas.lab.controller.client"):
        ControllerClient().ensure_running()
    assert not state["stopped"]
    assert "mas-lab control stop" in caplog.text


def test_drift_names_changed_environment_variables(temp_mas_home, monkeypatch):
    _fake_daemon(monkeypatch, code="current", env={"MAS_INFRA_REFS": "aaa", "OPENAI_API_KEY": "k"})
    import mas.lab.controller.client as client_mod

    monkeypatch.setattr(client_mod.cfg, "env_fingerprint", lambda: {"OPENAI_API_KEY": "k", "MAS_CTL_MODEL": "m"})
    assert ControllerClient().drift() == ["its environment differs for MAS_CTL_MODEL, MAS_INFRA_REFS"]


def test_ensure_running_restarts_idle_daemon_on_env_drift(temp_mas_home, monkeypatch):
    state = _fake_daemon(monkeypatch, code="current", env={"MAS_INFRA_REFS": "aaa"})
    ControllerClient().ensure_running(restart_stale=True)
    assert state["stopped"] and state["started"]


def test_env_fingerprint_hashes_run_relevant_variables_only():
    from mas.lab.controller import config as cfg

    fp = cfg.env_fingerprint(
        {"MAS_INFRA_REFS": "x", "OPENAI_API_KEY": "secret", "MAS_CONTROLLER_PORT": "9000", "HOME": "/h"}
    )
    assert set(fp) == {"MAS_INFRA_REFS", "OPENAI_API_KEY"}
    assert "secret" not in fp.values()


def test_code_fingerprint_tracks_source_changes(tmp_path, monkeypatch):
    import mas
    from mas.lab.controller import config as cfg

    pkg = tmp_path / "mas"
    pkg.mkdir()
    module = pkg / "engine.py"
    module.write_text("A = 1\n", encoding="utf-8")
    monkeypatch.setattr(mas, "__path__", [str(pkg)])
    before = cfg.code_fingerprint()
    assert cfg.code_fingerprint() == before
    module.write_text("A = 22\n", encoding="utf-8")
    assert cfg.code_fingerprint() != before
