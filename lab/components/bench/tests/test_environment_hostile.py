#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
"""Hostile probes: isolation, closed-unless-listed, schema vs compile."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from mas.lab.benchmark.environment.attach import refuse_environment_in_workers
from mas.lab.benchmark.environment.clock import RunClock
from mas.lab.benchmark.environment.engine import RunEnvironment, _ScopedFixtures
from mas.lab.benchmark.environment.events import EventLog
from mas.lab.benchmark.environment.fixtures import (
    FixtureRegistry,
    _decode_cmdline,
    _fixture_env,
    _same_command,
    command_digest,
    reap_stale_fixtures,
)
from mas.lab.benchmark.environment.model import (
    Limits,
    RunCoordinates,
    compile_clock,
    compile_environment,
    compile_fixture,
)
from mas.lab.benchmark.environment.step import EnvironmentContext
from mas.lab.benchmark.environment.variables import Variables
from mas.library.lab.steps.environment.shell_action import ShellActionStep
from mas.library.lab.steps.environment.threshold_alarm import ThresholdAlarmStep
from mas.library.standard.lib.environment_port import reset_environment_port, set_environment_port
from mas.library.standard.plugins.governance.environment import EnvironmentPlugin


def _registry(tmp_path: Path) -> FixtureRegistry:
    return FixtureRegistry(base_env=dict(os.environ), output_dir=tmp_path, work_dir=tmp_path)


def test_reap_does_not_kill_when_the_recorded_digest_is_wrong(tmp_path: Path) -> None:
    proc = subprocess.Popen(["sleep", "40"], start_new_session=True)
    pidfile = tmp_path / ".environment" / "fixtures" / "run" / "issuer.json"
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(
        json.dumps(
            {
                "pid": proc.pid,
                "pgid": proc.pid,
                "command": ["sleep", "40"],
                "command_sha256": "sha256:deadbeef",
                "name": "issuer",
            }
        ),
        encoding="utf-8",
    )
    try:
        assert reap_stale_fixtures(tmp_path) == []
        os.kill(proc.pid, 0)
    finally:
        proc.kill()
        proc.wait()


def test_reap_ignores_json_inside_a_fixture_data_dir(tmp_path: Path) -> None:
    proc = subprocess.Popen(["sleep", "41"], start_new_session=True)
    planted = tmp_path / ".environment" / "fixtures" / "run" / "issuer" / "status.json"
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text(
        json.dumps(
            {
                "pid": proc.pid,
                "pgid": proc.pid,
                "command": ["sleep", "41"],
                "command_sha256": command_digest(("sleep", "41")),
                "name": "issuer",
            }
        ),
        encoding="utf-8",
    )
    try:
        assert reap_stale_fixtures(tmp_path) == []
        os.kill(proc.pid, 0)
    finally:
        proc.kill()
        proc.wait()


def test_same_command_does_not_treat_sha256sum_as_sh() -> None:
    rec = ("python3", "fixtures/issuer.py", "--port", "58895")
    live = ["/opt/homebrew/Frameworks/Python.app/Contents/MacOS/Python", "fixtures/issuer.py", "--port", "58895"]
    assert _same_command(live, rec)
    assert not _same_command([live[0], "other.py", "fixtures/issuer.py", "--port", "58895"], rec)
    assert not _same_command(["/usr/bin/sha256sum"], ("sh",))


def test_proc_cmdline_bytes_decode_to_str() -> None:
    parts = _decode_cmdline(b"python3\0fixtures/issuer.py\0--port\0" + b"58895\0")
    assert parts == ["python3", "fixtures/issuer.py", "--port", "58895"]
    assert all(isinstance(part, str) for part in parts)


def test_empty_fixture_keys_do_not_push_clock_to_every_run(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    hits: list[str] = []
    spec = compile_fixture({"name": "clk", "command": ["sleep", "30"], "clock": {"path": "/clock"}})
    handle = reg.start(spec, scope_key="run-b")
    handle.port, handle.host, handle.started = 9, "127.0.0.1", True
    orig = reg.push_clock

    def spy(now_s, *, scope_keys=None):
        hits.append(repr(scope_keys))
        return orig(now_s, scope_keys=scope_keys)

    reg.push_clock = spy  # type: ignore[method-assign]
    env = RunEnvironment(compile_environment({"clock": {"kind": "virtual"}}), run=RunCoordinates(), work_dir=tmp_path)
    env.fixtures, env.fixture_keys = reg, ()
    env._on_clock_advance(1.0)
    assert hits == ["()"]
    reg.close_all(force=True)


def test_stop_and_url_do_not_fall_back_to_a_sibling_runs_unique_fixture(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    spec = compile_fixture({"name": "svc", "command": ["sleep", "36"]})
    reg.start(spec, scope_key="run-b")
    env_a = RunEnvironment(compile_environment({}), run=RunCoordinates(), work_dir=tmp_path)
    env_a.fixtures, env_a.fixture_keys = reg, ("run-a",)
    scoped = _ScopedFixtures(env_a)
    scoped.stop("svc")
    assert reg.url("svc", scope_key="run-b")
    with pytest.raises(KeyError):
        scoped.url("svc")
    reg.close_all(force=True)


def test_start_once_join_keeps_a_ref_so_stop_does_not_kill_the_other_holder(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    spec = compile_fixture({"name": "shared", "command": ["sleep", "37"], "scope": "scenario"})
    first = reg.start_once(spec, scope_key="scenario-1")
    second = reg.start_once(spec, scope_key="scenario-1")
    assert first is second
    assert first.refs == 2
    env = RunEnvironment(compile_environment({}), run=RunCoordinates(), work_dir=tmp_path)
    env.fixtures, env.fixture_keys = reg, ("scenario-1",)
    _ScopedFixtures(env).stop("shared")
    assert reg.url("shared", scope_key="scenario-1")
    reg.close_all(force=True)


def test_clock_start_duration_compiles() -> None:
    spec = compile_clock({"kind": "virtual", "start": "5s"})
    assert spec.start_s == 5.0
    env = compile_environment({"clock": {"kind": "virtual", "start": "5s"}})
    assert env.clock.start_s == 5.0


def test_unknown_do_type_is_refused_at_compile() -> None:
    with pytest.raises((ValueError, TypeError), match="unknown environment step type"):
        compile_environment({"triggers": [{"id": "t", "when": {"on": "tool_call"}, "do": [{"type": "sheel"}]}]})


def test_threshold_alarm_in_a_trigger_does_not_emit_without_a_metric(tmp_path: Path) -> None:
    env = RunEnvironment(
        compile_environment(
            {
                "triggers": [
                    {
                        "id": "t",
                        "when": {"on": "tool_call"},
                        "do": [{"type": "threshold_alarm", "config": {"metric": "score", "value": 99}}],
                    }
                ]
            }
        ),
        run=RunCoordinates(),
        work_dir=tmp_path,
    )
    env.on_transition("tool_call", tool="x")
    assert not any(rec["name"] == "user.alarm" for rec in env.events.records())


@pytest.mark.asyncio
async def test_threshold_alarm_act_stays_quiet() -> None:
    engine = SimpleNamespace(deliver_event=MagicMock())
    real = EnvironmentContext(
        run=RunCoordinates(),
        clock=RunClock(),
        fixtures=SimpleNamespace(),
        vars=Variables(),
        events=EventLog(clock=RunClock(), run=RunCoordinates(), limits=Limits()),
        limits=Limits(),
        engine=engine,
    )
    await ThresholdAlarmStep("t", {"metric": "score", "value": 99}).act(real)
    engine.deliver_event.assert_not_called()


def test_worker_processes_refuse_named_actions_and_recorded_replay() -> None:
    actions = SimpleNamespace(
        environment={"actions": {"revoke": [{"type": "emit", "config": {"name": "x"}}]}},
        get_scenario=lambda sid: SimpleNamespace(environment={}),
    )
    with pytest.raises(ValueError, match="worker_processes: 1"):
        refuse_environment_in_workers(exp=actions, plan=[("s", {"id": "q"}, 0)])
    recorded = SimpleNamespace(
        environment={"replay": "recorded"},
        get_scenario=lambda sid: SimpleNamespace(environment={}),
    )
    with pytest.raises(ValueError, match="worker_processes: 1"):
        refuse_environment_in_workers(exp=recorded, plan=[("s", {"id": "q"}, 0)])


def test_empty_session_id_is_nobody_not_everybody() -> None:
    plugin = EnvironmentPlugin()
    made: list = []

    class Manager:
        def control(self, *, capability):
            made.append(capability)
            return SimpleNamespace()

    class Port:
        def triggers(self):
            return ()

        def bind_control(self, control, *, session_id="", entry_agent=""):
            return None

    token = set_environment_port(Port())  # type: ignore[arg-type]
    try:
        plugin.bind_session(SimpleNamespace(session_id="", entry_agent_id=""), manager=Manager())
    finally:
        reset_environment_port(token)
    assert made
    cap = made[0]
    assert cap.session_ids == frozenset()
    assert cap.allows("steer", "other") is False


def test_fixture_env_does_not_copy_the_host_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = compile_fixture({"name": "x", "command": ["true"]})
    monkeypatch.setenv("MYSQL_PWD", "hunter2")
    monkeypatch.setenv("REDIS_URL", "redis://:hunter2@localhost:6379")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    env = _fixture_env(spec, os.environ)
    assert "MYSQL_PWD" not in env
    assert "REDIS_URL" not in env
    assert "OPENAI_API_KEY" not in env


@pytest.mark.asyncio
async def test_shell_does_not_inherit_api_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    out = tmp_path / "env.txt"
    ctx = EnvironmentContext(
        run=RunCoordinates(),
        clock=RunClock(),
        fixtures=SimpleNamespace(),
        vars=Variables(),
        events=EventLog(clock=RunClock(), run=RunCoordinates(), limits=Limits()),
        limits=Limits(action_timeout_s=5),
        work_dir=tmp_path,
    )
    result = await ShellActionStep("sh", {"command": f"printenv OPENAI_API_KEY > {out}"}).act(ctx)
    assert result.outcome in {"ok", "failed"}
    text = out.read_text(encoding="utf-8") if out.exists() else ""
    assert "sk-secret" not in text
