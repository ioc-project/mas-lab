#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
from __future__ import annotations

import json
import os
import signal
import subprocess
import textwrap
import time
from pathlib import Path

import pytest
from mas.lab.benchmark.environment.fixtures import FixtureRegistry, command_digest, reap_stale_fixtures
from mas.lab.benchmark.environment.model import compile_fixture

SERVER = textwrap.dedent(
    """
    import os, time
    from http.server import BaseHTTPRequestHandler, HTTPServer
    port = int(os.environ.get("PORT", "0"))
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        def log_message(self, *a):
            return
    HTTPServer(("127.0.0.1", port), H).serve_forever()
    """
)


def _spec(command: list[str], **kwargs):
    return compile_fixture({"name": "issuer", "kind": "process", "command": command, "port": "auto", **kwargs})


def test_start_health_stop(tmp_path: Path) -> None:
    script = tmp_path / "server.py"
    script.write_text(SERVER, encoding="utf-8")
    registry = FixtureRegistry(base_env=dict(os.environ), output_dir=tmp_path, work_dir=tmp_path)
    spec = _spec(["python3", str(script)], health={"path": "/", "timeout_s": 5})
    handle = registry.start(spec, scope_key="run")
    assert handle.port
    assert registry.url("issuer").startswith("http://127.0.0.1:")
    registry.stop("issuer")
    assert handle.started is False


def test_own_process_group(tmp_path: Path) -> None:
    script = tmp_path / "child.py"
    script.write_text(
        "import os, time, subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    registry = FixtureRegistry(base_env=dict(os.environ), output_dir=tmp_path, work_dir=tmp_path)
    spec = _spec(["python3", str(script)])
    handle = registry.start(spec)
    assert handle.pgid
    registry.stop("issuer")
    time.sleep(0.2)
    with pytest.raises(OSError):
        os.killpg(handle.pgid, 0)


def test_pidfile_reap_matching_and_unrelated(tmp_path: Path) -> None:
    leftover_proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    leftover = leftover_proc.pid
    if leftover:
        pidfile = tmp_path / ".environment" / "fixtures" / "run" / "issuer.json"
        pidfile.parent.mkdir(parents=True, exist_ok=True)
        pidfile.write_text(
            json.dumps(
                {
                    "pid": leftover,
                    "pgid": leftover,
                    "command": ["sleep", "30"],
                    "command_sha256": command_digest(("sleep", "30")),
                    "name": "issuer",
                }
            ),
            encoding="utf-8",
        )
        # Unrelated pidfile with a live pid but wrong digest is not killed.
        other = tmp_path / ".environment" / "fixtures" / "run" / "other.json"
        other.write_text(
            json.dumps(
                {
                    "pid": leftover,
                    "pgid": leftover,
                    "command": ["not-this"],
                    "command_sha256": command_digest(("not-this",)),
                    "name": "other",
                }
            ),
            encoding="utf-8",
        )
        reaped = reap_stale_fixtures(tmp_path)
        assert "issuer" in reaped
        assert "other" not in reaped
        try:
            os.kill(leftover, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_keep_running_survives_close_all(tmp_path: Path) -> None:
    script = tmp_path / "server.py"
    script.write_text(SERVER, encoding="utf-8")
    registry = FixtureRegistry(base_env=dict(os.environ), output_dir=tmp_path, work_dir=tmp_path)
    spec = _spec(["python3", str(script)], health={"path": "/", "timeout_s": 5}, keep_running=True)
    handle = registry.start(spec, scope_key="run")
    registry.release("issuer", force=False)
    assert handle.started is True
    registry.close_all(force=False)
    assert handle.started is True
    registry.close_all(force=True)
    assert handle.started is False


def test_cached_runs_start_nothing(tmp_path: Path) -> None:
    registry = FixtureRegistry(base_env=dict(os.environ), output_dir=tmp_path, work_dir=tmp_path)
    assert registry._handles == {}


def test_start_once_scope_key_is_instance_id(tmp_path: Path) -> None:
    from mas.lab.benchmark.environment.attach import start_run_fixtures
    from mas.lab.benchmark.environment.model import compile_environment

    script = tmp_path / "server.py"
    script.write_text(SERVER, encoding="utf-8")
    registry = FixtureRegistry(base_env=dict(os.environ), output_dir=tmp_path, work_dir=tmp_path)
    spec = compile_environment(
        {
            "fixtures": [
                {
                    "name": "issuer",
                    "kind": "process",
                    "scope": "item",
                    "command": ["python3", str(script)],
                    "port": "auto",
                    "health": {"path": "/", "timeout_s": 5},
                }
            ]
        }
    )
    start_run_fixtures(
        spec,
        registry,
        run_key="s/item1/r1",
        item_key="s/item1",
        scenario_key="s",
    )
    pidfile = tmp_path / ".environment" / "fixtures" / "s" / "item1" / "issuer.json"
    assert pidfile.is_file()
    assert not (tmp_path / ".environment" / "fixtures" / "item" / "issuer.json").exists()
    registry.stop("issuer")


def test_next_run_fixture_start_does_not_use_closed_log(tmp_path: Path) -> None:
    from mas.lab.benchmark.environment.engine import RunEnvironment
    from mas.lab.benchmark.environment.model import RunCoordinates, compile_environment

    script = tmp_path / "server.py"
    script.write_text(SERVER, encoding="utf-8")
    registry = FixtureRegistry(base_env=dict(os.environ), output_dir=tmp_path, work_dir=tmp_path)
    spec = compile_environment(
        {
            "fixtures": [
                {
                    "name": "issuer",
                    "kind": "process",
                    "scope": "run",
                    "command": ["python3", str(script)],
                    "port": "auto",
                    "health": {"path": "/", "timeout_s": 5},
                }
            ]
        }
    )
    env1 = RunEnvironment(
        spec,
        run=RunCoordinates(experiment="e", scenario="s", item="i1", run="r1"),
        path=tmp_path / "e1.jsonl",
        work_dir=tmp_path,
        output_dir=tmp_path,
        fixtures=registry,
    )
    registry.start(spec.fixtures[0], scope_key="s/i1/r1")
    registry.stop("issuer")
    env1.close()
    env2 = RunEnvironment(
        spec,
        run=RunCoordinates(experiment="e", scenario="s", item="i2", run="r1"),
        path=tmp_path / "e2.jsonl",
        work_dir=tmp_path,
        output_dir=tmp_path,
        fixtures=registry,
    )
    registry.start(spec.fixtures[0], scope_key="s/i2/r1")
    names = [row["name"] for row in env2.events.records()]
    assert "fixture.starting" in names
    assert "fixture.ready" in names
    env2.close()
    registry.close_all(force=True)


def test_two_run_keys_do_not_share_a_process(tmp_path: Path) -> None:
    script = tmp_path / "server.py"
    script.write_text(SERVER, encoding="utf-8")
    registry = FixtureRegistry(base_env=dict(os.environ), output_dir=tmp_path, work_dir=tmp_path)
    spec = _spec(["python3", str(script)], health={"path": "/", "timeout_s": 5})
    first = registry.start(spec, scope_key="s/i1/r1")
    second = registry.start(spec, scope_key="s/i2/r1")
    assert first.pid != second.pid
    assert first.port != second.port
    assert registry.url("issuer", scope_key="s/i1/r1") != registry.url("issuer", scope_key="s/i2/r1")
    registry.stop("issuer", scope_key="s/i1/r1")
    assert first.started is False
    assert second.started is True
    registry.stop("issuer", scope_key="s/i2/r1")


def test_stopped_run_does_not_block_the_next_run_url(tmp_path: Path) -> None:
    script = tmp_path / "server.py"
    script.write_text(SERVER, encoding="utf-8")
    registry = FixtureRegistry(base_env=dict(os.environ), output_dir=tmp_path, work_dir=tmp_path)
    spec = _spec(["python3", str(script)], health={"path": "/", "timeout_s": 5})
    registry.start(spec, scope_key="s/i1/r1")
    registry.stop("issuer", scope_key="s/i1/r1")
    second = registry.start(spec, scope_key="s/i2/r1")
    assert registry.url("issuer") == registry.url("issuer", scope_key="s/i2/r1")
    assert registry.url("issuer").endswith(f":{second.port}")
    registry.stop("issuer", scope_key="s/i2/r1")


def test_command_interpolation_uses_this_run_issuer_not_the_sibling(tmp_path: Path) -> None:
    script = tmp_path / "server.py"
    script.write_text(SERVER, encoding="utf-8")
    registry = FixtureRegistry(base_env=dict(os.environ), output_dir=tmp_path, work_dir=tmp_path)
    issuer = _spec(["python3", str(script)], health={"path": "/", "timeout_s": 5})
    dependent = compile_fixture(
        {
            "name": "payments",
            "kind": "process",
            "command": ["sleep", "30", "${fixture.issuer.url}"],
        }
    )
    first = registry.start(issuer, scope_key="s/i1/r1")
    second = registry.start(issuer, scope_key="s/i2/r1")
    try:
        payments = registry.start(dependent, scope_key="s/i1/r1")
        assert str(first.port) in payments.command[-1]
        assert str(second.port) not in payments.command[-1]
    finally:
        registry.close_all(force=True)


def test_stopped_scope_key_does_not_keep_a_url(tmp_path: Path) -> None:
    script = tmp_path / "server.py"
    script.write_text(SERVER, encoding="utf-8")
    registry = FixtureRegistry(base_env=dict(os.environ), output_dir=tmp_path, work_dir=tmp_path)
    spec = _spec(["python3", str(script)], health={"path": "/", "timeout_s": 5})
    registry.start(spec, scope_key="s/i1/r1")
    registry.stop("issuer", scope_key="s/i1/r1")
    with pytest.raises(KeyError):
        registry.url("issuer", scope_key="s/i1/r1")


def test_start_run_fixtures_writes_this_run_urls_onto_env_vars(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from mas.lab.benchmark.environment.attach import start_run_fixtures
    from mas.lab.benchmark.environment.model import compile_environment
    from mas.lab.benchmark.environment.variables import Variables

    script = tmp_path / "server.py"
    script.write_text(SERVER, encoding="utf-8")
    env_spec = compile_environment(
        {
            "fixtures": [
                {
                    "name": "issuer",
                    "kind": "process",
                    "command": ["python3", str(script)],
                    "port": "auto",
                    "scope": "run",
                    "health": {"path": "/", "timeout_s": 5},
                }
            ]
        }
    )
    registry = FixtureRegistry(base_env=dict(os.environ), output_dir=tmp_path, work_dir=tmp_path)
    env_a = SimpleNamespace(vars=Variables(), fixture_keys=())
    env_b = SimpleNamespace(vars=Variables(), fixture_keys=())
    try:
        start_run_fixtures(env_spec, registry, run_key="s/i1/r1", item_key="s/i1", scenario_key="s", env=env_a)
        start_run_fixtures(env_spec, registry, run_key="s/i2/r1", item_key="s/i2", scenario_key="s", env=env_b)
        assert env_a.vars["fixture.issuer.url"] != env_b.vars["fixture.issuer.url"]
        assert env_a.vars["fixture.issuer.url"] == registry.url("issuer", scope_key="s/i1/r1")
        assert env_b.vars["fixture.issuer.url"] == registry.url("issuer", scope_key="s/i2/r1")
    finally:
        registry.close_all(force=True)


def test_kind_step_starts_and_stops(tmp_path: Path) -> None:
    spec = compile_fixture(
        {
            "name": "seed",
            "kind": "step",
            "start": {"type": "emit", "config": {"name": "user.up"}},
            "stop": {"type": "emit", "config": {"name": "user.down"}},
        }
    )
    registry = FixtureRegistry(base_env=dict(os.environ), output_dir=tmp_path, work_dir=tmp_path)
    handle = registry.start(spec, scope_key="run-a")
    assert handle.started is True
    registry.stop("seed", scope_key="run-a")
    assert handle.started is False


def test_kind_step_without_start_is_refused() -> None:
    with pytest.raises(ValueError, match="requires start and stop"):
        compile_fixture({"name": "seed", "kind": "step"})


def test_item_environment_cannot_declare_fixtures() -> None:
    from mas.lab.benchmark.environment.attach import blocks_for_run

    with pytest.raises(ValueError, match="cannot declare fixtures"):
        blocks_for_run(
            exp=None,
            scenario=None,
            item={"environment": {"fixtures": [{"name": "issuer", "kind": "process"}]}},
        )
