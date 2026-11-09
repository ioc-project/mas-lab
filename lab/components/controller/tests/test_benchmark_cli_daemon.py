#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Benchmark CLI always submits via controller daemon."""

from __future__ import annotations

import time

from click.testing import CliRunner


def test_benchmark_run_background_returns_worker_id(temp_mas_home, sample_lab, monkeypatch):
    import mas.lab.controller.client as client_mod
    from mas.lab.cli.commands import benchmark as benchmark_mod

    calls: dict = {}

    class FakeClient:
        def ensure_running(self, **kw):
            calls["ensure"] = True

        def call(self, method, params=None, timeout=30.0):
            if method == "submit_benchmark":
                return {"worker_id": "worker-bg-1"}
            raise AssertionError(method)

    monkeypatch.setattr(client_mod, "ControllerClient", FakeClient)

    runner = CliRunner()
    result = runner.invoke(
        benchmark_mod.run_cmd,
        [str(sample_lab / "experiments" / "smoke.yaml"), "--dry-run", "-b"],
    )
    assert result.exit_code == 0
    assert result.output.strip() == "worker-bg-1"
    assert calls.get("ensure")


def test_benchmark_run_blocking_follows_worker(temp_mas_home, sample_lab, monkeypatch):
    import mas.lab.controller.client as client_mod
    from mas.lab.cli.commands import benchmark as benchmark_mod

    class FakeClient:
        def ensure_running(self, **kw):
            return None

        def call(self, method, params=None, timeout=30.0):
            if method == "submit_benchmark":
                return {"worker_id": "worker-block-1"}
            raise AssertionError(method)

    def fake_follow(worker_id, poll=0.5, stream=True):
        assert worker_id == "worker-block-1"
        assert stream is True
        return {"status": "completed", "exit_code": 0, "stdout": "Configuration valid\n"}

    monkeypatch.setattr(client_mod, "ControllerClient", FakeClient)
    monkeypatch.setattr(client_mod, "follow_worker", fake_follow)

    runner = CliRunner()
    result = runner.invoke(
        benchmark_mod.run_cmd,
        [str(sample_lab / "experiments" / "smoke.yaml"), "--dry-run"],
    )
    assert result.exit_code == 0


def test_benchmark_run_blocking_failure_exit_code(temp_mas_home, sample_lab, monkeypatch):
    import mas.lab.controller.client as client_mod
    from mas.lab.cli.commands import benchmark as benchmark_mod

    class FakeClient:
        def ensure_running(self, **kw):
            return None

        def call(self, method, params=None, timeout=30.0):
            return {"worker_id": "worker-fail-1"}

    monkeypatch.setattr(client_mod, "ControllerClient", FakeClient)
    monkeypatch.setattr(
        client_mod,
        "follow_worker",
        lambda *a, **k: {"status": "failed", "exit_code": 1},
    )

    runner = CliRunner()
    result = runner.invoke(
        benchmark_mod.run_cmd,
        [str(sample_lab / "experiments" / "smoke.yaml"), "--dry-run"],
    )
    assert result.exit_code == 1


def test_benchmark_dry_run_worker_captures_stdout(temp_mas_home, sample_lab, monkeypatch):
    """End-to-end: daemon worker runs dry-run and exposes stdout for polling."""
    import mas.lab.controller.daemon as daemon_mod
    from mas.lab.controller.api import ControllerAPI

    api = ControllerAPI()
    monkeypatch.setattr(api.manifests, "refresh", lambda: None)
    api.manifests._libraries = {"demo": sample_lab}
    daemon_mod._api = api

    async def fake_run_benchmark(**kwargs):
        print("Configuration valid")
        return True

    monkeypatch.setattr("mas.lab.benchmark.engine.run_benchmark", fake_run_benchmark)

    wid = api.submit_benchmark(
        {
            "experiment_yaml": str(sample_lab / "experiments" / "smoke.yaml"),
            "dry_run": True,
            "progress": False,
        }
    )["worker_id"]

    deadline = time.time() + 10
    detail = None
    while time.time() < deadline:
        detail = api.get_worker(wid)
        if detail and detail["status"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(0.05)

    assert detail is not None
    assert detail["status"] == "completed"
    assert "Configuration valid" in (detail.get("stdout") or "")


def test_follow_worker_streams_deltas(temp_mas_home, monkeypatch, capsys):
    from mas.lab.controller import client as client_mod

    states = [
        {"status": "running", "stdout": "line1\n", "stderr": ""},
        {"status": "running", "stdout": "line1\nline2\n", "stderr": "warn\n"},
        {"status": "completed", "stdout": "line1\nline2\n", "stderr": "warn\n", "exit_code": 0},
    ]

    class FakeClient:
        def ensure_running(self, **kw):
            return None

        def call(self, method, params=None, timeout=30.0):
            if method == "get_worker":
                return states.pop(0) if states else states[-1]
            raise AssertionError(method)

    monkeypatch.setattr(client_mod, "ControllerClient", FakeClient)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    detail = client_mod.follow_worker("w-stream", poll=0.01, stream=True)
    captured = capsys.readouterr()
    assert "line2" in captured.out
    assert "warn" in captured.err
    assert detail["status"] == "completed"
