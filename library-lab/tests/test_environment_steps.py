#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
from __future__ import annotations

import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest
from mas.lab.benchmark.environment.engine import RunEnvironment
from mas.lab.benchmark.environment.model import RunCoordinates, compile_environment
from mas.lab.benchmark.pipeline.keys import run_key_from_parts
from mas.library.lab.steps.environment.checkpoint import CheckpointStep
from mas.library.lab.steps.environment.clock_advance import ClockAdvanceStep
from mas.library.lab.steps.environment.emit import EmitStep
from mas.library.lab.steps.environment.fixture_control import FixtureRestartStep, FixtureStopStep
from mas.library.lab.steps.environment.hitl_answer import HitlAnswerStep
from mas.library.lab.steps.environment.http_call import HttpCallStep
from mas.library.lab.steps.environment.inject import InjectStep
from mas.library.lab.steps.environment.kill_switch import KillSwitchStep
from mas.library.lab.steps.environment.pause import PauseStep
from mas.library.lab.steps.environment.resume import ResumeStep
from mas.library.lab.steps.environment.shell_action import ShellActionStep
from mas.library.lab.steps.environment.sleep import SleepStep
from mas.library.lab.steps.environment.steer import SteerStep
from mas.library.lab.steps.environment.threshold_alarm import ThresholdAlarmStep


class _Control:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def steer(self, session_id: str, **kwargs):
        self.calls.append(("steer", session_id, kwargs))

    def enqueue_input(self, session_id: str, text: str, **kwargs):
        self.calls.append(("enqueue", session_id, text, kwargs))

    def pause(self, session_id: str, **kwargs):
        self.calls.append(("pause", session_id, kwargs))

    def resume(self, session_id: str, **kwargs):
        self.calls.append(("resume", session_id, kwargs))

    def snapshot(self, session_id: str, **kwargs):
        self.calls.append(("snapshot", session_id, kwargs))

    def persist(self, session_id: str, **kwargs):
        self.calls.append(("persist", session_id, kwargs))

    def cancel_inflight(self, session_id: str, **kwargs):
        self.calls.append(("cancel", session_id, kwargs))


class _Fixtures:
    def __init__(self, url: str = "http://127.0.0.1:9") -> None:
        self.url_value = url
        self.calls: list[tuple] = []

    def url(self, name: str) -> str:
        return self.url_value

    def port(self, name: str) -> int:
        return 9

    def host(self, name: str) -> str:
        return "127.0.0.1"

    def restart(self, name: str):
        self.calls.append(("restart", name))

    def stop(self, name: str) -> None:
        self.calls.append(("stop", name))


def _ctx(**kwargs):
    spec = compile_environment({"clock": {"kind": kwargs.pop("clock", "virtual")}})
    env = RunEnvironment(spec, run=RunCoordinates(), path=None)
    env.control = kwargs.get("control")
    env.session_id = kwargs.get("session_id", "s1")
    if "fixtures" in kwargs:
        env.fixtures = kwargs["fixtures"]
    return env.context()


@pytest.mark.asyncio
async def test_clock_advance_and_emit() -> None:
    ctx = _ctx()
    out = await ClockAdvanceStep("adv", {"by": "1s"}).act(ctx)
    assert out.payload["virtual_s"] == 1.0
    with pytest.raises(ValueError, match="by"):
        await ClockAdvanceStep("adv", {}).act(ctx)
    await EmitStep("e", {"name": "ping", "payload": {"status": 1}}).act(ctx)
    names = [r["name"] for r in ctx.events.records()]
    assert "user.ping" in names
    with pytest.raises(ValueError, match="name"):
        await EmitStep("e", {}).act(ctx)


@pytest.mark.asyncio
async def test_sleep_and_shell(tmp_path) -> None:
    ctx = _ctx(clock="wall")
    ctx.work_dir = tmp_path
    slept = await SleepStep("s", {"for": 0.01}).act(ctx)
    assert slept.payload["duration_ms"] == 10.0
    with pytest.raises(ValueError, match="for"):
        await SleepStep("s", {}).act(ctx)
    ok = await ShellActionStep("sh", {"command": "true"}).act(ctx)
    assert ok.outcome == "ok"
    bad = await ShellActionStep("sh", {"command": "false"}).act(ctx)
    assert bad.outcome == "failed"
    with pytest.raises(ValueError, match="command"):
        await ShellActionStep("sh", {}).act(ctx)


@pytest.mark.asyncio
async def test_http_call_loopback() -> None:
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            return

    server = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        host, port = server.server_address[:2]
        url = f"http://{host}:{port}/health"
        ctx = _ctx()
        ctx.fixtures = _Fixtures(url.rsplit("/", 1)[0])
        direct = await HttpCallStep("h", {"method": "GET", "url": url, "expect_status": 200}).act(ctx)
        assert direct.payload["status"] == 200
        via = await HttpCallStep(
            "h", {"method": "GET", "fixture": "issuer", "path": "/health", "expect_status": 200}
        ).act(ctx)
        assert via.payload["status"] == 200
        with pytest.raises(ValueError, match="fixture"):
            await HttpCallStep("h", {}).act(ctx)
    finally:
        server.shutdown()


@pytest.mark.asyncio
async def test_fixture_and_control_steps() -> None:
    fixtures = _Fixtures()
    control = _Control()
    ctx = _ctx(control=control, fixtures=fixtures)
    ctx.fixtures = fixtures
    await FixtureRestartStep("r", {"fixture": "issuer"}).act(ctx)
    await FixtureStopStep("s", {"name": "issuer"}).act(ctx)
    assert fixtures.calls == [("restart", "issuer"), ("stop", "issuer")]
    with pytest.raises(ValueError):
        await FixtureRestartStep("r", {}).act(ctx)
    with pytest.raises(ValueError):
        await FixtureStopStep("s", {}).act(ctx)
    await SteerStep("st", {"text": "go"}).act(ctx)
    await InjectStep("i", {"text": "hi"}).act(ctx)
    await HitlAnswerStep("h", {"content": "yes"}).act(ctx)
    await PauseStep("p", {"reason": "wait"}).act(ctx)
    await ResumeStep("r", {}).act(ctx)
    await CheckpointStep("c", {"label": "x", "persist": True}).act(ctx)
    await KillSwitchStep("k", {"reason": "stop"}).act(ctx)
    verbs = [row[0] for row in control.calls]
    assert verbs == ["steer", "enqueue", "enqueue", "pause", "resume", "snapshot", "persist", "cancel", "persist"]
    assert control.calls[6][2].get("auto_stop") is True
    assert control.calls[-1][2].get("auto_stop") is True
    bare = _ctx()
    with pytest.raises(PermissionError):
        await SteerStep("st", {}).act(bare)


@pytest.mark.asyncio
async def test_kill_switch_pauses_when_persist_fails() -> None:
    class Boom(_Control):
        def persist(self, session_id: str, **kwargs):
            raise RuntimeError("no store")

    control = Boom()
    ctx = _ctx(control=control)
    out = await KillSwitchStep("k", {"reason": "stop"}).act(ctx)
    assert out.payload["persisted"] == 0
    assert [row[0] for row in control.calls] == ["cancel", "pause"]
    assert control.calls[-1][2].get("reason") == "stop"


@pytest.mark.asyncio
async def test_threshold_alarm_execute_and_act() -> None:
    ctx = _ctx()
    await ThresholdAlarmStep("t", {"event": "user.alarm"}).act(ctx)
    key = run_key_from_parts(experiment="e", scenario="s", item="i", run="r1")
    pipe = SimpleNamespace(run_key=key, data={"score": 2.0})
    out = await ThresholdAlarmStep("t", {"metric": "score", "threshold": 1.0, "event": "user.alarm"}).execute(pipe)
    assert out.metadata["fired"] is True
    missing = SimpleNamespace()
    quiet = await ThresholdAlarmStep("t", {"value": 0, "threshold": 5}).execute(missing)
    assert quiet.metadata["fired"] is False
    no_data = SimpleNamespace(run_key=key)
    via_attr = await ThresholdAlarmStep("t", {"metric": "score", "threshold": 1.0}).execute(no_data)
    assert via_attr.metadata["fired"] is False
    with pytest.raises(PermissionError):
        await CheckpointStep("c", {}).act(_ctx())
    no_engine = _ctx()
    no_engine.engine = None
    await EmitStep("e", {"name": "user.direct"}).act(no_engine)


@pytest.mark.asyncio
async def test_http_call_json_and_expect_status() -> None:
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(400)
            self.end_headers()

        def log_message(self, *a):
            return

    server = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        host, port = server.server_address[:2]
        url = f"http://{host}:{port}/admin"
        ctx = _ctx()
        ctx.limits = replace(ctx.limits, max_http_body_bytes=8)
        with pytest.raises(ValueError, match="max_http_body"):
            await HttpCallStep("h", {"method": "POST", "url": url, "json": {"too": "big"}}).act(ctx)
        ctx.limits = replace(ctx.limits, max_http_body_bytes=10_000)
        failed = await HttpCallStep(
            "h",
            {"method": "POST", "url": url, "json": {"ok": True}, "expect_status": 200},
        ).act(ctx)
        assert failed.outcome == "failed"
        assert failed.payload["status"] == 400
        down = await HttpCallStep("h", {"method": "GET", "url": "http://127.0.0.1:1/nope"}).act(ctx)
        assert down.outcome == "failed"
    finally:
        server.shutdown()


@pytest.mark.asyncio
async def test_http_call_does_not_follow_a_redirect() -> None:
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "https://example.invalid/x")
            self.end_headers()

        def log_message(self, *a):
            return

    server = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        host, port = server.server_address[:2]
        url = f"http://{host}:{port}/go"
        out = await HttpCallStep("h", {"method": "GET", "url": url}).act(_ctx())
        assert out.payload["status"] == 302
    finally:
        server.shutdown()


def test_environment_package_and_artifact_type() -> None:
    from mas.library.lab.artifact_types import Environment
    from mas.library.lab.steps import environment as env_steps

    assert Environment.stream == "custom.environment"
    assert Environment.plot == "timeline"
    assert env_steps.HttpCallStep.type == "http_call"
