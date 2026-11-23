#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
"""Regression tests for the lab environment: timers, event log, fixtures, steps."""

from __future__ import annotations

import asyncio
import http.server
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest
from mas.lab.benchmark.environment.attach import write_fixture_llm_infra
from mas.lab.benchmark.environment.control import EnvironmentControlAdapter
from mas.lab.benchmark.environment.engine import (
    RunEnvironment,
    compile_environment,
    environment_digest,
    write_environment_control,
)
from mas.lab.benchmark.environment.fixtures import FixtureRegistry
from mas.lab.benchmark.environment.limits import assert_url_allowed
from mas.lab.benchmark.environment.model import RunCoordinates, compile_fixture
from mas.lab.benchmark.environment.variables import Variables


def _env(raw: dict, tmp_path: Path) -> RunEnvironment:
    return RunEnvironment(compile_environment(raw), run=RunCoordinates(), work_dir=tmp_path)


def _names(env: RunEnvironment) -> list[str]:
    return [rec["name"] for rec in env.events.records()]


# --- timers -----------------------------------------------------------------------


def test_after_timer_fires_once_its_predecessor_fired(tmp_path: Path) -> None:
    env = _env(
        {
            "clock": {"kind": "virtual"},
            "triggers": [{"id": "a", "when": {"on": "tool_call"}, "do": [{"type": "emit", "config": {"name": "a"}}]}],
            "timers": [{"id": "b", "after": "a", "do": [{"type": "emit", "config": {"name": "b"}}]}],
        },
        tmp_path,
    )
    env.on_transition("tool_call", tool="x")
    assert "user.b" in _names(env)


def test_after_delay_counts_from_the_predecessor(tmp_path: Path) -> None:
    env = _env(
        {
            "clock": {"kind": "virtual", "start_s": 100},
            "triggers": [{"id": "a", "when": {"on": "tool_call"}, "do": []}],
            "timers": [{"id": "b", "after": "a", "delay": "10s", "do": [{"type": "emit", "config": {"name": "b"}}]}],
        },
        tmp_path,
    )
    env.on_transition("tool_call", tool="x")
    assert "timer.fired" not in _names(env)
    env.clock.advance(9)
    assert "timer.fired" not in _names(env)
    env.clock.advance(1)
    assert "timer.fired" in _names(env)


def test_every_timer_repeats_and_catches_up(tmp_path: Path) -> None:
    env = _env({"clock": {"kind": "virtual"}, "timers": [{"id": "t", "every": "10s", "do": []}]}, tmp_path)
    env.clock.advance(35)
    assert _names(env).count("timer.fired") == 4  # 0, 10, 20, 30


@pytest.mark.parametrize(
    "raw, match",
    [
        ({"timers": [{"id": "t", "do": []}]}, "timer needs"),
        ({"timers": [{"id": "t", "every": "0s", "do": []}]}, "every"),
        ({"timers": [{"id": "t", "after": "nope", "do": []}]}, "unknown trigger"),
        ({"timers": [{"id": "t", "after": "t", "do": []}]}, "itself"),
        ({"triggers": [{"id": "t", "when": {"on": "tool_call", "at": "5s"}}]}, "only apply to timers"),
    ],
)
def test_timer_misconfiguration_is_refused(raw: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        compile_environment(raw)


def test_event_trigger_name_is_folded_like_the_event(tmp_path: Path) -> None:
    env = _env(
        {
            "triggers": [
                {
                    "id": "t",
                    "when": {"on": "event", "event": "Alarm"},
                    "do": [{"type": "emit", "config": {"name": "seen"}}],
                }
            ]
        },
        tmp_path,
    )
    assert env.deliver_event("ALARM") is True
    env.on_transition("tool_call")
    assert "user.seen" in _names(env)


def test_skipped_is_noted_once_and_only_for_matching_calls(tmp_path: Path) -> None:
    env = _env({"triggers": [{"id": "t", "when": {"on": "tool_call", "tool": "pay"}, "do": []}]}, tmp_path)
    for _ in range(5):
        env.on_transition("tool_call", tool="other")
    env.on_transition("tool_call", tool="pay")
    for _ in range(3):
        env.on_transition("tool_call", tool="pay")
    assert _names(env).count("trigger.skipped") == 1


# --- event log ----------------------------------------------------------------------


def test_event_cap_does_not_fail_the_run(tmp_path: Path) -> None:
    env = _env(
        {
            "limits": {"max_events_per_run": 6},
            "triggers": [{"id": "t", "when": {"on": "tool_call", "occurrence": "every"}, "do": []}],
        },
        tmp_path,
    )
    for _ in range(50):
        env.on_transition("tool_call", tool="x")  # must not raise
    env.close()
    assert env.events.records()[-1]["name"] == "environment.closed"


def test_action_failure_is_one_record(tmp_path: Path) -> None:
    env = _env(
        {"triggers": [{"id": "t", "when": {"on": "tool_call"}, "do": [{"type": "http_call", "config": {}}]}]}, tmp_path
    )
    env.on_transition("tool_call", tool="x")
    failed = [rec for rec in env.events.records() if rec["name"] == "action.failed"]
    assert len(failed) == 1
    assert failed[0]["payload"]["error"] == "valueerror"


def test_reentrant_step_does_not_deadlock(tmp_path: Path) -> None:
    env = _env(
        {"triggers": [{"id": "t", "when": {"on": "tool_call"}, "do": [{"type": "emit", "config": {"name": "x"}}]}]},
        tmp_path,
    )
    done = threading.Event()

    def run() -> None:
        env.on_transition("tool_call", tool="a")
        env.on_transition("tool_call", tool="a")
        done.set()

    threading.Thread(target=run, daemon=True).start()
    assert done.wait(5)


def test_concurrent_events_are_not_lost(tmp_path: Path) -> None:
    env = _env(
        {"triggers": [{"id": "t", "when": {"on": "event", "event": "ping", "occurrence": "every"}, "do": []}]}, tmp_path
    )
    threads = [threading.Thread(target=lambda: [env.deliver_event("ping") for _ in range(50)]) for _ in range(4)]
    spins = threading.Event()

    def pump() -> None:
        while not spins.is_set():
            env.on_transition("tool_call")

    pumper = threading.Thread(target=pump)
    pumper.start()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    spins.set()
    pumper.join()
    env.on_transition("tool_call")
    assert _names(env).count("event.delivered") == 200


# --- digest / yaml --------------------------------------------------------------------


def test_digest_covers_actions_and_fixture_details() -> None:
    base = {"triggers": [{"id": "t", "when": {"on": "tool_call"}, "do": []}]}
    assert environment_digest(
        {**base, "actions": {"a": [{"type": "emit", "config": {"name": "x"}}]}}
    ) != environment_digest(base)
    fx = {"name": "f", "command": ["x"], "health": {"path": "/a"}}
    assert environment_digest({"fixtures": [fx]}) != environment_digest(
        {"fixtures": [{**fx, "health": {"path": "/b"}}]}
    )
    assert environment_digest(
        {**base, "clock": {"kind": "virtual", "epoch": "2030-01-01T00:00:00Z"}}
    ) != environment_digest({**base, "clock": {"kind": "virtual"}})


def test_control_yaml_survives_hostile_script(tmp_path: Path) -> None:
    import yaml

    script = "break tool_call pay_fare\ncommands\n  checkpoint\nend\n# done: 'quoted' and: colons"
    path = write_environment_control(tmp_path / "c.yaml", script=script)
    doc = yaml.safe_load(path.read_text())
    gdb = next(item["gdb"] for item in doc["spec"]["governance"] if isinstance(item, dict))
    assert gdb["script"].strip("\n") == script
    # and the real Control loader accepts exactly what we wrote
    from mas.ctl.session.flavour_control.documents import validate_control_document

    validate_control_document(doc, path)


def test_llm_infra_yaml_is_escaped(tmp_path: Path) -> None:
    import yaml
    from secret_fakes import bound_secrets

    with bound_secrets({"lab": "any"}):
        path = write_fixture_llm_infra(tmp_path / "i.yaml", "http://127.0.0.1:1", name="a: b\n  c")
    assert yaml.safe_load(path.read_text())["metadata"]["name"] == "a: b\n  c"


# --- variables / url / control ------------------------------------------------------------


def test_interpolation_is_single_pass() -> None:
    vars_ = Variables({"a": "${b}", "b": "SECRET"})
    assert vars_.interpolate("x ${a}") == "x ${b}"
    assert vars_.interpolate("cost $$5 and ${b}") == "cost $5 and SECRET"
    with pytest.raises(ValueError, match="unknown"):
        vars_.interpolate("{missing}")


def test_url_allow_list_requires_http() -> None:
    assert assert_url_allowed("http://127.0.0.1:9/x")
    for bad in ("ftp://127.0.0.1/x", "file:///etc/passwd", "gopher://localhost/"):
        with pytest.raises(ValueError):
            assert_url_allowed(bad)
    with pytest.raises(ValueError):
        assert_url_allowed("http://localhost@evil.example/")


def test_http_call_does_not_follow_a_redirect_off_the_allow_list(tmp_path: Path) -> None:
    class Handler(http.server.BaseHTTPRequestHandler):
        hits: list[str] = []

        def do_POST(self) -> None:  # noqa: N802
            self.send_response(302)
            self.send_header("Location", "http://example.invalid/steal")
            self.end_headers()

        def log_message(self, *args) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        env = _env(
            {
                "triggers": [
                    {
                        "id": "t",
                        "when": {"on": "tool_call"},
                        "do": [
                            {
                                "type": "http_call",
                                "config": {"url": f"http://127.0.0.1:{server.server_port}/", "expect_status": 302},
                            }
                        ],
                    }
                ]
            },
            tmp_path,
        )
        env.on_transition("tool_call", tool="x")
        done = [rec for rec in env.events.records() if rec["name"] == "action.done"]
        assert done and done[0]["payload"]["status"] == 302
    finally:
        server.shutdown()


def test_control_adapter_refuses_what_the_contract_lacks() -> None:
    class Contract:
        def steer(self, session_id, *, text="", **kw):
            return ("steer", session_id, text)

    adapter = EnvironmentControlAdapter(Contract())
    assert adapter.steer("s", text="hi") == ("steer", "s", "hi")
    with pytest.raises(PermissionError, match="pause"):
        adapter.pause("s")


# --- steps -----------------------------------------------------------------------------------


def test_sleep_is_cut_short_by_the_action_timeout(tmp_path: Path) -> None:
    env = _env(
        {
            "limits": {"action_timeout_s": 0.2, "max_sleep_s": 30},
            "triggers": [{"id": "t", "when": {"on": "tool_call"}, "do": [{"type": "sleep", "config": {"for": 20}}]}],
        },
        tmp_path,
    )
    started = time.monotonic()
    env.on_transition("tool_call", tool="x")
    assert time.monotonic() - started < 5
    assert "action.timeout" in _names(env)


def test_shell_timeout_kills_the_group(tmp_path: Path) -> None:
    """A grandchild must be gone when the action returns; no timing against a clock."""
    pidfile = tmp_path / "grandchild.pid"
    command = f"sleep 43 & echo $! > {pidfile}; wait"
    env = _env(
        {
            "limits": {"action_timeout_s": 0.5},
            "triggers": [
                {"id": "t", "when": {"on": "tool_call"}, "do": [{"type": "shell", "config": {"command": command}}]}
            ],
        },
        tmp_path,
    )
    env.on_transition("tool_call", tool="x")
    assert pidfile.exists(), "the command never started"
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 5
    alive = True
    while alive and time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            time.sleep(0.05)
        except ProcessLookupError:
            alive = False
    assert not alive, "the grandchild outlived the action"
    assert "action.failed" in _names(env) or "action.timeout" in _names(env)


def test_threshold_alarm_refuses_unknown_operator() -> None:
    from mas.library.lab.steps.environment.threshold_alarm import _crosses

    with pytest.raises(ValueError, match="op"):
        _crosses(1.0, "=>", 0.0)


# --- fixtures -------------------------------------------------------------------------------------


def _registry(tmp_path: Path) -> FixtureRegistry:
    return FixtureRegistry(base_env=dict(os.environ), output_dir=tmp_path, work_dir=tmp_path)


def test_stopped_fixture_can_be_restarted_on_the_same_url(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    spec = compile_fixture({"name": "svc", "command": ["sleep", "30"]})
    reg.start(spec, scope_key="k")
    url = reg.url("svc", scope_key="k")
    reg.stop("svc", scope_key="k")
    with pytest.raises(KeyError):
        reg.url("svc", scope_key="k")
    reg.restart("svc", scope_key="k")
    assert reg.url("svc", scope_key="k") == url
    reg.close_all(force=True)


def test_restart_keeps_other_holders(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    spec = compile_fixture({"name": "svc", "command": ["sleep", "30"]})
    reg.start(spec, scope_key="k")
    reg.start(spec, scope_key="k")
    reg.restart("svc", scope_key="k")
    reg.release("svc", scope_key="k")
    assert reg.url("svc", scope_key="k")  # one holder left
    reg.release("svc", scope_key="k")
    with pytest.raises(KeyError):
        reg.url("svc", scope_key="k")


def test_failed_health_leaves_no_process_behind(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    spec = compile_fixture({"name": "bad", "command": ["sleep", "31"], "port": "auto", "health": {"timeout_s": 0.4}})
    with pytest.raises(RuntimeError, match="failed to start"):
        reg.start(spec, scope_key="k")
    time.sleep(0.2)
    alive = subprocess.run(["pgrep", "-f", "sleep 31"], capture_output=True, text=True).stdout.strip()
    assert alive == ""
    assert not list((tmp_path / ".environment" / "fixtures").rglob("bad.json"))


def test_process_exit_fails_fast_with_its_log(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    spec = compile_fixture(
        {
            "name": "dies",
            "command": ["sh", "-c", "echo boom-from-fixture >&2; exit 3"],
            "port": "auto",
            "health": {"timeout_s": 30},
        }
    )
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="exited with code 3.*boom-from-fixture"):
        reg.start(spec, scope_key="k")
    assert time.monotonic() - started < 10


def test_slow_start_does_not_block_other_fixtures(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    slow = compile_fixture({"name": "slow", "command": ["sleep", "30"], "port": "auto", "health": {"timeout_s": 5}})
    fast = compile_fixture({"name": "fast", "command": ["sleep", "30"]})
    t = threading.Thread(target=lambda: pytest.raises(RuntimeError, reg.start, slow, scope_key="a"))
    t.start()
    time.sleep(0.2)
    began = time.monotonic()
    reg.start(fast, scope_key="b")
    # The slow fixture is still probing (5s); a registry-wide lock would hold this start behind it.
    assert time.monotonic() - began < 4.0 and t.is_alive()
    t.join()
    reg.close_all(force=True)


def test_clock_push_is_scoped_to_this_run(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    pushed: list[str] = []
    import urllib.request

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            pushed.append(self.server.name)  # type: ignore[attr-defined]
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args) -> None:
            pass

    servers = []
    for key in ("run-a", "run-b"):
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        srv.name = key  # type: ignore[attr-defined]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        spec = compile_fixture({"name": "clocked", "command": ["sleep", "30"], "port": srv.server_port, "clock": True})
        handle = reg.register(spec, scope_key=key)
        handle.port, handle.started = srv.server_port, True
    reg.push_clock(1.0, scope_keys=("run-a",))
    assert pushed == ["run-a"]
    del urllib
    for srv in servers:
        srv.shutdown()


def test_stop_group_never_signals_own_group() -> None:
    import os

    from mas.lab.benchmark.environment.fixtures import _stop_group

    assert _stop_group(os.getpgrp(), 0.1) is False
    assert _stop_group(0, 0.1) is False
    assert _stop_group(1, 0.1) is False
    assert asyncio  # keep import used for the loop-bound steps above


# --- second round: checkpoints, services, figures, bindings -------------------------------------


def test_debug_script_file_is_read_inside_the_experiment(tmp_path: Path) -> None:
    from mas.lab.benchmark.schedule.whatif import read_debug_script

    (tmp_path / "break.gdb").write_text("break tool_call pay\n", encoding="utf-8")
    assert read_debug_script({"script_file": "break.gdb"}, tmp_path) == "break tool_call pay\n"
    assert read_debug_script({"script": "inline", "script_file": "break.gdb"}, tmp_path) == "inline"
    with pytest.raises(ValueError, match="absolute"):
        read_debug_script({"script_file": "/etc/passwd"}, tmp_path)
    with pytest.raises(ValueError, match="leaves"):
        read_debug_script({"script_file": "../x.gdb"}, tmp_path)
    with pytest.raises(FileNotFoundError):
        read_debug_script({"script_file": "nope.gdb"}, tmp_path)


def test_apply_triggers_keeps_script_from_file(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from mas.lab.benchmark.schedule.whatif import apply_session_checkpoint_triggers

    (tmp_path / "b.gdb").write_text("break tool_call x\n", encoding="utf-8")
    spec = SimpleNamespace(id="s", debug={"script_file": "b.gdb"}, environment={}, produce_checkpoints=[])
    apply_session_checkpoint_triggers([spec], [], base_dir=tmp_path)
    assert spec.environment["script"] == "break tool_call x\n"


def test_published_checkpoint_is_whole_or_absent(tmp_path: Path) -> None:
    from mas.lab.benchmark.schedule.whatif import publish_named_checkpoints, published_checkpoint_path

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "c-1.checkpoint.json").write_text('{"half": ', encoding="utf-8")
    assert (
        publish_named_checkpoints(run_checkpoint_dir=run_dir, output_dir=tmp_path, names=["c"], missing_ok=True) == []
    )
    assert not published_checkpoint_path(tmp_path, "c", index=1).is_file()
    with pytest.raises(ValueError, match="not valid JSON"):
        publish_named_checkpoints(run_checkpoint_dir=run_dir, output_dir=tmp_path, names=["c"])
    (run_dir / "c-1.checkpoint.json").write_text('{"ok": 1}', encoding="utf-8")
    done = publish_named_checkpoints(run_checkpoint_dir=run_dir, output_dir=tmp_path, names=["c"])
    assert done and all(p.read_text() == '{"ok": 1}' for p in done)
    assert not list((tmp_path / "checkpoints").glob(".tmp-*"))


def test_service_stop_does_not_signal_a_recycled_pid(tmp_path: Path) -> None:
    from mas.lab.benchmark.service_manager import ServiceManager

    services = tmp_path / "services.yaml"
    services.write_text("services:\n  svc:\n    backend: process\n    command: [sleep, '31']\n", encoding="utf-8")
    bystander = subprocess.Popen(["sleep", "32"], start_new_session=True)
    try:
        mgr = ServiceManager(services, template_vars={"output_dir": str(tmp_path)})
        svc = mgr._services["svc"]
        # A stale record that now names an unrelated live process.
        mgr._write_pidfile(svc, bystander.pid)
        assert mgr._process_stop(svc) is True
        time.sleep(0.2)
        assert bystander.poll() is None
        assert not mgr._pidfile(svc).exists()
    finally:
        bystander.kill()
        bystander.wait()


def test_timeline_svg_escapes_hostile_records() -> None:
    from mas.library.lab.ui.timeline import render_svg

    events = [{"name": "fixture.ready", "kind": "fixture", "source": {"id": '"><script>x</script>'}}]
    svg = render_svg("timeline", {"events": events}, title="<img onerror=x>")
    assert "<script>" not in svg and "<img" not in svg


def test_palette_refuses_markup() -> None:
    from mas.lab.benchmark.pipeline.figure import resolve_palette

    assert resolve_palette(["#0072b2", "rebeccapurple", "rgb(1, 2, 3)"])
    with pytest.raises(ValueError, match="palette colour"):
        resolve_palette(['red" onload="x'])


def test_binding_typo_is_an_error_not_none() -> None:
    from mas.lab.benchmark.pipeline.binding import resolve

    with pytest.raises(ValueError, match="unknown binding root"):
        resolve("$experimnt.name", {"experiment": {}})
    assert resolve("$row.value", {"experiment": {}}) is None


def test_allocated_port_is_retried_once_when_the_process_dies_at_once(tmp_path: Path) -> None:
    marker = tmp_path / "tried"
    script = (
        f"if [ -f {marker} ]; then exec python3 -m http.server {{port}} --bind 127.0.0.1; "
        f"else touch {marker}; exit 1; fi"
    )
    reg = _registry(tmp_path)
    spec = compile_fixture(
        {"name": "web", "command": ["sh", "-c", script], "port": "auto", "health": {"path": "/", "timeout_s": 15}}
    )
    try:
        handle = reg.start(spec, scope_key="k")
        assert handle.started and marker.exists()
    finally:
        reg.close_all(force=True)


def test_a_fixed_port_is_not_retried(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    spec = compile_fixture({"name": "w", "command": ["sh", "-c", "exit 1"], "port": 45999, "health": {"timeout_s": 5}})
    with pytest.raises(RuntimeError, match="exited with code 1"):
        reg.start(spec, scope_key="k")


def test_scenario_scalars_are_not_split_into_characters() -> None:
    from mas.lab.lab.config.scenario import _debug_block, _parse_resume_from, _str_list

    assert _str_list("producer", "depends_on") == ["producer"]
    assert _str_list(["a", "b"], "depends_on") == ["a", "b"]
    with pytest.raises(ValueError, match="depends_on"):
        _str_list({"a": 1}, "depends_on")
    with pytest.raises(ValueError, match="debug"):
        _debug_block(["break"])
    assert _parse_resume_from("ck", "2") == ("ck", 2)
    for bad in ("abc", 0, -1, True, 1.5):
        with pytest.raises(ValueError, match="resume_index"):
            _parse_resume_from("ck", bad)
    with pytest.raises(ValueError, match="resume_from.index"):
        _parse_resume_from({"id": "ck", "index": 0}, None)


def test_realtime_must_be_a_real_boolean() -> None:
    from mas.lab.benchmark.pipeline.core import PipelineStep

    with pytest.raises(ValueError, match="realtime"):
        PipelineStep.from_dict({"type": "sleep", "name": "s", "realtime": "false"})


# --- third round ---------------------------------------------------------------------------------


def test_checkpoint_keys_do_not_collide_after_sanitising() -> None:
    from mas.lab.benchmark.schedule.whatif import checkpoint_key

    assert checkpoint_key({"id": "plain-1"}, 0) == "plain-1/r1"
    assert checkpoint_key({"id": "a/b"}, 0) != checkpoint_key({"id": "a_b"}, 0)
    assert checkpoint_key({"id": "a/b"}, 0) == checkpoint_key({"id": "a/b"}, 0)


def test_series_named_like_a_figure_key_keeps_its_label() -> None:
    from mas.lab.benchmark.pipeline.figure import FigureStyle

    style = FigureStyle.from_config({"labels": {"title": "T", "series": {"x": "Scenario X", "title": "Scenario T"}}})
    assert style.title == "T"
    assert style.label("x") == "Scenario X" and style.label("title") == "Scenario T"
    with pytest.raises(ValueError, match="labels.series"):
        FigureStyle.from_config({"labels": {"series": ["x"]}})


def test_run_setup_unwinds_newest_first_and_survives_a_failing_cleanup() -> None:
    from mas.lab.benchmark.environment.attach import run_with_cleanups

    order: list[str] = []

    def boom() -> None:
        order.append("boom")
        raise RuntimeError("cleanup failed")

    def body(pending: list) -> None:
        pending.append(lambda: order.append("stream"))
        pending.append(boom)
        pending.append(lambda: order.append("fixtures"))
        raise ValueError("setup failed")

    with pytest.raises(ValueError, match="setup failed"):
        run_with_cleanups(body)
    assert order == ["fixtures", "boom", "stream"]


def test_run_setup_hands_over_cleanup_once_the_body_owns_it() -> None:
    from mas.lab.benchmark.environment.attach import run_with_cleanups

    ran: list[str] = []

    def body(pending: list) -> str:
        pending.append(lambda: ran.append("early"))
        pending.clear()  # the body's own try/finally takes over
        raise KeyError("later failure")

    with pytest.raises(KeyError):
        run_with_cleanups(body)
    assert ran == []
    assert run_with_cleanups(lambda pending: "ok") == "ok"


def test_pidfile_stays_where_it_was_written_when_the_output_dir_changes(tmp_path: Path) -> None:
    from mas.lab.benchmark.service_manager import ServiceManager

    services = tmp_path / "services.yaml"
    services.write_text("services:\n  svc:\n    backend: process\n    command: [sleep, '33']\n", encoding="utf-8")
    first, second = tmp_path / "first", tmp_path / "second"
    mgr = ServiceManager(services, template_vars={"output_dir": str(first)})
    svc = mgr._services["svc"]
    mgr._write_pidfile(svc, 999999)
    written = mgr._pidfile(svc)
    mgr._template_vars["output_dir"] = str(second)
    assert mgr._pidfile(svc) == written and written.parent.parent == first


def test_editing_a_fixture_script_changes_the_run_digest(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from mas.lab.benchmark.environment.attach import digest_for_run

    script = tmp_path / "fixtures" / "issuer.py"
    script.parent.mkdir()
    script.write_text("print(1)\n", encoding="utf-8")
    exp = SimpleNamespace(environment={"fixtures": [{"name": "issuer", "command": ["python3", "fixtures/issuer.py"]}]})
    scenario = SimpleNamespace(environment={})
    first = digest_for_run(exp=exp, scenario=scenario, item=None, work_dir=tmp_path)
    assert first == digest_for_run(exp=exp, scenario=scenario, item=None, work_dir=tmp_path)
    script.write_text("print(2)\n", encoding="utf-8")
    assert first != digest_for_run(exp=exp, scenario=scenario, item=None, work_dir=tmp_path)
    # A file outside the experiment is never read into the digest.
    outside = SimpleNamespace(environment={"fixtures": [{"name": "x", "command": ["cat", "/etc/hosts"]}]})
    assert digest_for_run(exp=outside, scenario=scenario, item=None, work_dir=tmp_path)


def test_keep_running_is_per_services_bundle(tmp_path: Path) -> None:
    from mas.lab.benchmark.service_manager import is_keep_running, mark_keep_running

    a, b = tmp_path / "a.yaml", tmp_path / "b.yaml"
    mark_keep_running("db", a)
    assert is_keep_running("db", a) and not is_keep_running("db", b)


def _spawn_fixture(script: str) -> tuple[subprocess.Popen, int]:
    root = Path(__file__).resolve().parents[4] / "labs" / "revocation.lab" / "fixtures" / script
    proc = subprocess.Popen(["python3", str(root), "--port", "0"], stdout=subprocess.PIPE, text=True)
    line = proc.stdout.readline().strip()  # type: ignore[union-attr]
    return proc, int(line.split("=", 1)[1])


def test_scripted_model_finish_reasons_and_streaming() -> None:
    import json
    import urllib.request

    proc, port = _spawn_fixture("scripted_model.py")
    try:

        def ask(messages: list, **extra) -> bytes:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                data=json.dumps({"messages": messages, **extra}).encode(),
                method="POST",
            )
            return urllib.request.urlopen(req, timeout=5).read()

        first = json.loads(ask([{"role": "user", "content": "go"}]))
        assert first["choices"][0]["finish_reason"] == "tool_calls"
        done = json.loads(ask([{"role": "tool"}] * 3))
        assert done["choices"][0]["finish_reason"] == "stop"
        streamed = ask([{"role": "user", "content": "go"}], stream=True).decode()
        assert streamed.rstrip().endswith("data: [DONE]") and '"tool_calls"' in streamed
        bad = urllib.request.Request(f"http://127.0.0.1:{port}/x", data=b"{not json", method="POST")
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(bad, timeout=5)
        assert err.value.code == 400
    finally:
        proc.kill()
        proc.wait()


def test_issuer_refuses_a_malformed_clock_push() -> None:
    import urllib.request

    proc, port = _spawn_fixture("issuer.py")
    try:
        for body in (b"{not json", b'{"now": "soon"}'):
            req = urllib.request.Request(f"http://127.0.0.1:{port}/clock", data=body, method="POST")
            with pytest.raises(urllib.error.HTTPError) as err:
                urllib.request.urlopen(req, timeout=5)
            assert err.value.code == 400
        ok = urllib.request.Request(f"http://127.0.0.1:{port}/clock", data=b'{"now": 5}', method="POST")
        assert urllib.request.urlopen(ok, timeout=5).status == 200
    finally:
        proc.kill()
        proc.wait()


def test_fixture_stop_in_one_run_leaves_the_other_runs_fixture_alone(tmp_path: Path) -> None:
    from mas.lab.benchmark.environment.engine import _ScopedFixtures

    reg = _registry(tmp_path)
    spec = compile_fixture({"name": "svc", "command": ["sleep", "34"]})
    reg.start(spec, scope_key="run-a")
    reg.start(spec, scope_key="run-b")
    env_a = _env({}, tmp_path)
    env_a.fixtures, env_a.fixture_keys = reg, ("run-a",)
    _ScopedFixtures(env_a).stop("svc")
    with pytest.raises(KeyError):
        reg.url("svc", scope_key="run-a")
    assert reg.url("svc", scope_key="run-b")  # untouched
    _ScopedFixtures(env_a).restart("svc")
    assert reg.url("svc", scope_key="run-a")
    reg.close_all(force=True)


def test_scoped_stop_never_falls_back_to_another_scope(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    spec = compile_fixture({"name": "svc", "command": ["sleep", "35"]})
    reg.start(spec, scope_key="run-b")
    reg.stop("svc", scope_key="run-a")  # run-a never had it
    assert reg.url("svc", scope_key="run-b")
    reg.close_all(force=True)


def test_registry_survives_concurrent_registration(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    errors: list[BaseException] = []

    def churn(index: int) -> None:
        try:
            for round_ in range(40):
                spec = compile_fixture({"name": f"f{index}", "command": ["sleep", "1"]})
                handle = reg.register(spec, scope_key=f"k{index}-{round_}")
                reg._publish_vars(handle)
                reg._scope_vars(handle)
                reg.push_clock(1.0)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=churn, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_event_stream_is_not_buffered_without_observability_triggers(tmp_path: Path) -> None:
    class Stream:
        subscribed = 0

        def subscribe(self, callback):
            Stream.subscribed += 1
            return lambda: None

    quiet = _env({"triggers": [{"id": "t", "when": {"on": "tool_call"}, "do": []}]}, tmp_path)
    quiet.attach_event_stream(Stream())
    assert Stream.subscribed == 0
    watching = _env(
        {"triggers": [{"id": "o", "when": {"on": "observability", "match": {"kind": "x"}}, "do": []}]}, tmp_path
    )
    watching.attach_event_stream(Stream())
    assert Stream.subscribed == 1


def test_a_bad_environment_is_reported_before_any_run() -> None:
    from types import SimpleNamespace

    from mas.lab.benchmark.environment.attach import validate_environments

    scenario = SimpleNamespace(environment={"timers": [{"id": "t", "after": "ghost"}]})
    exp = SimpleNamespace(environment={}, get_scenario=lambda sid: scenario)
    with pytest.raises(ValueError, match=r"scenario 's1', item 'q1'.*unknown trigger"):
        validate_environments(exp=exp, plan=[("s1", {"id": "q1"}, 0), ("s1", {"id": "q1"}, 1)])
    fixture_item = {"id": "q2", "environment": {"fixtures": [{"name": "f", "command": ["x"]}]}}
    ok = SimpleNamespace(environment={}, get_scenario=lambda sid: SimpleNamespace(environment={}))
    with pytest.raises(ValueError, match="item 'q2'.*fixtures"):
        validate_environments(exp=ok, plan=[("s1", fixture_item, 0)])


class _Contract:
    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.calls: list[tuple] = []

    def steer(self, session_id, *, text="", **kw):
        self.calls.append(("steer", session_id, text))
        return (self.tag, session_id)

    def pause(self, session_id, *, reason="", **kw):
        self.calls.append(("pause", session_id, reason))


def test_default_session_is_the_first_bound_and_others_stay_addressable(tmp_path: Path) -> None:
    env = _env({}, tmp_path)
    env.bind_control(_Contract("first"), session_id="s1", entry_agent="desk")
    env.bind_control(_Contract("second"), session_id="s2", entry_agent="peer")
    assert env.session_id == "s1" and env._entry_agent == "desk"
    assert env.control.steer(env.session_id) == ("first", "s1")
    assert env.session_target(None)[1] == "s1"
    assert env.session_target("s2")[1] == "s2"  # by session id
    assert env.session_target("peer")[1] == "s2"  # by entry agent
    with pytest.raises(KeyError, match="no bound session"):
        env.session_target("ghost")
    env.bind_control(_Contract("refreshed"), session_id="s1", entry_agent="desk")
    assert env.control.steer("s1") == ("refreshed", "s1")


def test_a_step_can_name_the_session_it_acts_on(tmp_path: Path) -> None:
    first, second = _Contract("first"), _Contract("second")
    env = _env(
        {
            "triggers": [
                {
                    "id": "t",
                    "when": {"on": "tool_call"},
                    "do": [
                        {"type": "pause", "config": {"reason": "own"}},
                        {"type": "pause", "config": {"session": "peer", "reason": "other"}},
                        {"type": "steer", "config": {"session": "nobody", "text": "x"}},
                    ],
                }
            ]
        },
        tmp_path,
    )
    env.bind_control(first, session_id="s1", entry_agent="desk")
    env.bind_control(second, session_id="s2", entry_agent="peer")
    env.on_transition("tool_call", tool="x")
    assert first.calls == [("pause", "s1", "own")]
    assert second.calls == [("pause", "s2", "other")]
    failed = [r for r in env.events.records() if r["name"] == "action.failed"]
    assert len(failed) == 1 and failed[0]["payload"]["error"] == "keyerror"  # an unknown session is loud


def test_binding_miss_in_a_populated_root_warns_once(caplog) -> None:
    import logging

    from mas.lab.benchmark.pipeline import binding

    binding._WARNED.clear()
    bag = {"experiment": {"name": "x"}, "scenario": {"id": "s"}}
    with caplog.at_level(logging.WARNING):
        assert binding.resolve("$scenario.metadata.legend", bag) is None
        assert binding.resolve("$scenario.metadata.legend", bag) is None
        assert binding.resolve("$row.value", bag) is None  # empty root: routine, silent
    assert sum("resolved to nothing" in rec.message for rec in caplog.records) == 1


# --- fourth round ----------------------------------------------------------------------------------


def test_a_late_event_after_close_is_dropped_not_raised(tmp_path: Path) -> None:
    env = _env({}, tmp_path)
    env.close()
    assert env.deliver_event("late") is False  # a fold outliving the run must not crash its thread
    assert env.deliver_event("has spaces") is False
    env.on_transition("tool_call", tool="x")  # no raise either


def test_event_name_is_validated_before_it_is_logged(tmp_path: Path) -> None:
    env = _env({}, tmp_path)
    assert env.deliver_event("bad name!") is False
    assert env.deliver_event("fine") is True
    assert "event.emitted" in _names(env)


def test_wall_timer_steps_run_without_the_engine_lock(tmp_path: Path) -> None:
    owned: list[bool] = []
    env = _env({"timers": [{"id": "t", "at": "0.6s", "do": [{"type": "emit", "config": {"name": "x"}}]}]}, tmp_path)
    original = env._run_steps

    def spy(steps, **kwargs):
        owned.append(env._lock._is_owned())
        return original(steps, **kwargs)

    env._run_steps = spy  # type: ignore[method-assign]
    deadline = time.monotonic() + 5
    while not owned and time.monotonic() < deadline:
        time.sleep(0.05)
    env.close()
    assert owned == [False]  # holding it would deadlock against a control call from the run thread


def test_damaged_recorded_line_does_not_fail_the_live_run(tmp_path: Path) -> None:
    import json

    log = tmp_path / "environment.jsonl"
    rec = {"name": "event.emitted", "anchor": {"occurrence": "not-a-number"}, "payload": {"event": "user.x"}}
    log.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    env = RunEnvironment(
        compile_environment({"replay": "recorded", "triggers": [{"id": "t", "when": {"on": "tool_call"}, "do": []}]}),
        run=RunCoordinates(),
        path=log,
        work_dir=tmp_path,
    )
    env.on_transition("tool_call", tool="x")  # must not raise


def test_fixture_does_not_inherit_secrets_from_our_environment(monkeypatch) -> None:
    from mas.lab.benchmark.environment.fixtures import _fixture_env

    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("MY_SERVICE_TOKEN", "t")
    monkeypatch.setenv("KEEP_ME_KEY", "ok")
    monkeypatch.setenv("PLAIN_SETTING", "yes")
    spec = compile_fixture(
        {"name": "f", "command": ["x"], "passthrough": ["KEEP_ME_KEY"], "env": {"EXPLICIT_SECRET": "v"}}
    )
    env = _fixture_env(spec, os.environ)
    assert "OPENAI_API_KEY" not in env and "MY_SERVICE_TOKEN" not in env
    assert env["KEEP_ME_KEY"] == "ok" and env["EXPLICIT_SECRET"] == "v"
    assert "PLAIN_SETTING" not in env  # undeclared host keys stay out
    assert env["MAS_FIXTURE_NAME"] == "f"


def test_scope_keys_cannot_escape_the_fixture_directory(tmp_path: Path) -> None:
    from mas.lab.benchmark.environment.fixtures import _safe_scope

    for hostile in ("../../etc", "a/../../b", "/abs/path", "x/./y"):
        safe = _safe_scope(hostile)
        assert ".." not in safe.split("/") and not safe.startswith("/")
    assert _safe_scope("scenario/item1/r1") == "scenario/item1/r1"
    reg = _registry(tmp_path)
    spec = compile_fixture({"name": "svc", "command": ["sleep", "36"]})
    reg.start(spec, scope_key="../../escape")
    assert not (tmp_path.parent / "escape").exists()
    assert list((tmp_path / ".environment" / "fixtures").rglob("svc.json"))
    reg.close_all(force=True)


def test_probe_paths_are_normalised() -> None:
    spec = compile_fixture({"name": "f", "command": ["x"], "health": {"path": "health"}, "clock": {"path": "clock"}})
    assert spec.health.path == "/health" and spec.clock.path == "/clock"


def test_a_step_fixture_that_never_became_ready_is_stopped(tmp_path: Path) -> None:
    marker = tmp_path / "stopped"
    reg = _registry(tmp_path)
    spec = compile_fixture(
        {
            "name": "s",
            "kind": "step",
            "port": 45998,
            "health": {"timeout_s": 0.3},
            "start": {"type": "emit", "config": {"name": "up"}},
            "stop": {"type": "shell", "config": {"command": f"touch {marker}"}},
        }
    )
    with pytest.raises(RuntimeError, match="failed to start"):
        reg.start(spec, scope_key="k")
    assert marker.exists()


def test_dependency_status_is_read_from_the_same_item() -> None:
    from mas.lab.benchmark.schedule.whatif import row_status

    rows = [
        {"scenario": "p", "item_id": 1, "run": 1, "status": "error"},
        {"scenario": "p", "item_id": 2, "run": 1, "status": "ok"},  # a later, different item
    ]
    assert row_status(rows, "p", 1, 1) == "error"
    assert row_status(rows, "p", 2, 1) == "ok"
    assert row_status(rows, "p", 3, 1) == "ok"
    assert row_status(rows, "p", 1, 2) == "ok"


def test_clock_advance_step_does_not_deadlock_inside_a_running_loop(tmp_path: Path) -> None:
    """Inside the kernel's event loop a step runs on a worker thread while the run's thread holds
    the engine lock and waits. A step that advances the clock must not need that lock."""
    env = _env(
        {
            "clock": {"kind": "virtual"},
            "triggers": [
                {"id": "t", "when": {"on": "tool_call"}, "do": [{"type": "clock_advance", "config": {"by": "21s"}}]}
            ],
            "timers": [{"id": "later", "at": "10s", "do": [{"type": "emit", "config": {"name": "woke"}}]}],
        },
        tmp_path,
    )
    done = threading.Event()

    async def inside_a_loop() -> None:
        env.on_transition("tool_call", tool="pay")  # blocking call from a loop thread, like the kernel

    def run() -> None:
        asyncio.run(inside_a_loop())
        done.set()

    threading.Thread(target=run, daemon=True).start()
    assert done.wait(10), "clock_advance deadlocked against the engine lock"
    names = _names(env)
    assert "clock.advanced" in names and "timer.fired" in names and "action.done" in names


def test_a_timer_fires_once_under_concurrent_collection(tmp_path: Path) -> None:
    env = _env({"clock": {"kind": "virtual"}, "timers": [{"id": "t", "at": "1s", "do": []}]}, tmp_path)
    env.clock._virtual_s = 5.0
    threads = [threading.Thread(target=lambda: env._collect_due_timers(None)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert _names(env).count("timer.fired") == 1


def test_publishing_into_a_sealed_stream_does_not_fail_the_run() -> None:
    from mas.lab.benchmark.environment.attach import publish_environment_record
    from mas.lab.benchmark.pipeline.channel import InprocChannel, reset_stream_id, set_channel, set_stream_id

    channel = InprocChannel()
    set_channel(channel)
    token = set_stream_id("rerun/s/item/r1#1")
    try:
        publish_environment_record({"name": "clock.started"})
        channel.seal_sync("rerun/s/item/r1#1")  # an earlier batch in this process sealed it
        publish_environment_record({"name": "clock.advanced"})  # must not raise
    finally:
        reset_stream_id(token)
        set_channel(None)


def test_parallel_runs_do_not_write_each_others_fixture_events(tmp_path: Path) -> None:
    from mas.lab.benchmark.environment.attach import start_run_fixtures, stop_run_fixtures

    spec = compile_environment({"fixtures": [{"name": "svc", "command": ["sleep", "37"]}]})
    reg = _registry(tmp_path)
    envs = []
    for tag in ("a", "b"):
        env = RunEnvironment(
            spec, run=RunCoordinates(run=tag), path=tmp_path / f"{tag}.jsonl", work_dir=tmp_path, fixtures=reg
        )
        envs.append(env)
    for env, tag in zip(envs, ("a", "b"), strict=True):
        start_run_fixtures(spec, reg, run_key=f"run-{tag}", item_key="i", scenario_key="s", env=env)
    for tag in ("a", "b"):
        stop_run_fixtures(spec, reg, run_key=f"run-{tag}", item_key="i", scenario_key="s")
    for env in envs:
        env.close()
    for env in envs:
        fixture_events = [r["name"] for r in env.events.records() if r["name"].startswith("fixture.")]
        assert fixture_events == ["fixture.starting", "fixture.ready", "fixture.stopped"], fixture_events


def test_a_sealed_stream_starts_over_when_the_same_run_begins_again(tmp_path: Path) -> None:
    from mas.lab.benchmark.pipeline.channel import (
        get_channel,
        make_channel,
        reset_stream_id,
        set_channel,
    )
    from mas.lab.benchmark.pipeline.keys import run_key_from_parts
    from mas.lab.benchmark.pipeline.lifecycle import begin_run, terminal_run

    channel = make_channel(log_root=tmp_path / "streams")
    set_channel(channel)
    key = run_key_from_parts(experiment="e", scenario="s", item="i", run="r1")
    try:
        token = begin_run(key)
        channel.publish_sync(key.stream_id(), {"kind": "custom.environment", "n": 1})
        terminal_run(key, "ok")
        assert channel.is_sealed(key.stream_id())
        first_digest = channel.final_digest(key.stream_id())
        reset_stream_id(token)

        token = begin_run(key)  # the same experiment run again in this process
        assert not channel.is_sealed(key.stream_id())
        channel.publish_sync(key.stream_id(), {"kind": "custom.environment", "n": 2})  # no "is sealed"
        kinds = [frame.kind for frame in channel.replay(key.stream_id())]
        assert kinds.count("run.started") == 1  # not the old epoch's records
        terminal_run(key, "ok")
        assert channel.final_digest(key.stream_id()) != first_digest
        reset_stream_id(token)
        lines = (tmp_path / "streams").glob("*.jsonl")
        assert all(sum(1 for _ in open(path)) <= 4 for path in lines)  # the file did not keep growing
    finally:
        assert get_channel() is channel
        set_channel(None)


def test_reopening_an_open_or_unknown_stream_is_a_no_op() -> None:
    from mas.lab.benchmark.pipeline.channel import InprocChannel

    channel = InprocChannel()
    assert channel.reopen("nothing") is False
    channel.publish_sync("open", {"kind": "x"})
    assert channel.reopen("open") is False and len(channel.replay("open")) == 1


def test_same_command_ignores_the_resolved_program_name() -> None:
    from mas.lab.benchmark.environment.fixtures import _same_command

    rec = ("python3", "fixtures/issuer.py", "--port", "58895")
    live = ["/opt/homebrew/Frameworks/Python.app/Contents/MacOS/Python", "fixtures/issuer.py", "--port", "58895"]
    assert _same_command(live, rec)
    assert not _same_command([live[0], "fixtures/issuer.py", "--port", "1"], rec)  # other arguments
    assert not _same_command(["/usr/bin/vim", "notes.txt"], rec)
    assert _same_command(["/bin/sleep"], ("sleep",)) and not _same_command(["/bin/cat"], ("sleep",))
    assert not _same_command([], rec) and not _same_command(live, ())


def test_an_orphan_started_through_python3_is_reaped_after_the_runner_dies(tmp_path: Path) -> None:
    """A hard kill of the runner leaves its fixtures running; the next batch must reap them."""
    import json

    from mas.lab.benchmark.environment.fixtures import command_digest, reap_stale_fixtures

    command = ("python3", "-c", "import time; time.sleep(41)", "orphan-marker")
    proc = subprocess.Popen(list(command), start_new_session=True)
    try:
        pidfile = tmp_path / ".environment" / "fixtures" / "s" / "orphan.json"
        pidfile.parent.mkdir(parents=True)
        pidfile.write_text(
            json.dumps(
                {
                    "pid": proc.pid,
                    "pgid": proc.pid,
                    "command_sha256": command_digest(command),
                    "command": list(command),
                    "name": "orphan",
                }
            ),
            encoding="utf-8",
        )
        from mas.lab.benchmark.environment.fixtures import _command_of

        deadline = time.monotonic() + 10
        while "orphan-marker" not in " ".join(_command_of(proc.pid)) and time.monotonic() < deadline:
            time.sleep(0.05)  # until the child has exec'd: before that ps shows our own command line
        assert reap_stale_fixtures(tmp_path) == ["orphan"]
        assert proc.wait(timeout=5) is not None
    finally:
        proc.kill()
        proc.wait()


def test_kill_switch_reports_what_it_did(tmp_path: Path) -> None:
    class NoStore(_Contract):
        def cancel_inflight(self, session_id):
            return False  # nothing in flight at a tool result

        def persist(self, session_id, **kw):
            raise RuntimeError("persist requires a checkpoint store")

    class WithStore(_Contract):
        def cancel_inflight(self, session_id):
            return True

        def persist(self, session_id, **kw):
            return {"path": "x"}

    for contract, expected in (
        (NoStore("a"), {"cancelled": 0, "persisted": 0}),
        (WithStore("b"), {"cancelled": 1, "persisted": 1}),
    ):
        env = _env(
            {"triggers": [{"id": "k", "when": {"on": "tool_call"}, "do": [{"type": "kill_switch", "config": {}}]}]},
            tmp_path,
        )
        env.bind_control(contract, session_id="s1", entry_agent="a")
        env.on_transition("tool_call", tool="x")
        done = next(r for r in env.events.records() if r["name"] == "action.done")
        assert done["payload"] == expected
    assert not any(call[0] == "pause" for call in contract.calls)  # with a store it persisted, no fallback pause


def test_checkpoint_step_stops_the_session_by_default(tmp_path: Path) -> None:
    seen: list[dict] = []

    class Contract(_Contract):
        def snapshot(self, session_id, *, label="", auto_stop=False, **kw):
            seen.append({"label": label, "auto_stop": auto_stop})

    env = _env(
        {
            "triggers": [
                {"id": "c", "when": {"on": "tool_call"}, "do": [{"type": "checkpoint", "config": {"label": "mid"}}]}
            ]
        },
        tmp_path,
    )
    env.bind_control(Contract("c"), session_id="s1", entry_agent="a")
    env.on_transition("tool_call", tool="x")
    assert seen == [{"label": "mid", "auto_stop": True}]  # a snapshot of a running session is refused


def test_validate_reports_environment_problems_once_and_before_any_run(tmp_path: Path) -> None:
    import yaml
    from mas.lab.benchmark.environment.attach import environment_violations

    (tmp_path / "dataset.yaml").write_text(
        yaml.safe_dump(
            {
                "spec": {
                    "items": [{"id": "a"}, {"id": "b", "environment": {"fixtures": [{"name": "x", "command": ["y"]}]}}]
                }
            }
        ),
        encoding="utf-8",
    )
    data = {
        "experiment": {
            "environment": {},
            "scenarios": [
                {"id": "s1"},
                {"id": "s2", "environment": {"timers": [{"id": "t", "after": "ghost", "do": []}]}},
            ],
            "dataset": {"path": "dataset.yaml"},
        }
    }
    problems = environment_violations(data, tmp_path)
    assert any(
        "scenario 's2'" in p and "unknown trigger" in p and "item" not in p for p in problems
    )  # once, not per item
    assert any("scenario 's1', item 'b'" in p and "fixtures" in p for p in problems)
    assert len(problems) == 2
    assert environment_violations({"experiment": {"scenarios": [{"id": "ok"}]}}, tmp_path) == []
    assert environment_violations({"not": "an experiment"}, tmp_path) == []


def test_mas_lab_validate_path_raises_on_a_bad_environment(tmp_path: Path) -> None:
    from mas.lab.manifests.validator import ManifestValidationError, validate_manifest

    data = {
        "experiment": {
            "name": "x",
            "application": {"manifest": "./agent.yaml"},
            "scenarios": [{"id": "s", "overlays": {"logic": [], "control": [], "infra": []}}],
            "dataset": {"path": "./dataset.yaml"},
            "environment": {"timers": [{"id": "t", "every": "0s", "do": []}]},
        }
    }
    with pytest.raises(ManifestValidationError, match="environment of scenario 's'"):
        validate_manifest(data, source="e.yaml", kind="experiment", strict=True, resolve_refs=False, base_dir=tmp_path)


def test_a_run_folder_that_links_to_another_hash_is_stale(tmp_path: Path) -> None:
    from mas.lab.benchmark.schedule.run_batch.execute import _run_ref_is_stale

    assert _run_ref_is_stale(tmp_path, "abc") is False  # no pointer: nothing to compare
    (tmp_path / ".run_ref").write_text("abc\n", encoding="utf-8")
    assert _run_ref_is_stale(tmp_path, "abc") is False
    assert _run_ref_is_stale(tmp_path, "def") is True


def test_a_worker_process_batch_refuses_an_environment_it_could_not_run() -> None:
    from types import SimpleNamespace

    from mas.lab.benchmark.environment.attach import refuse_environment_in_workers

    with_env = SimpleNamespace(
        environment={"triggers": [{"id": "t", "when": {"on": "tool_call"}, "do": []}]},
        get_scenario=lambda sid: SimpleNamespace(environment={}),
    )
    with pytest.raises(ValueError, match="worker_processes: 1"):
        refuse_environment_in_workers(exp=with_env, plan=[("s", {"id": "q"}, 0)])
    without = SimpleNamespace(environment={}, get_scenario=lambda sid: SimpleNamespace(environment={}))
    refuse_environment_in_workers(exp=without, plan=[("s", {"id": "q"}, 0)])  # no environment: fine


# --- final hostile review ----------------------------------------------------------


def test_wall_timer_loop_survives_a_failing_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _env(
        {"timers": [{"id": "t", "every": "50ms", "do": [{"type": "emit", "config": {"name": "x"}}]}]},
        tmp_path,
    )
    calls: list[int] = []
    real = env._run_steps

    def flaky(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("a step blew up outside its own guard")
        return real(*args, **kwargs)

    monkeypatch.setattr(env, "_run_steps", flaky)
    try:
        deadline = time.monotonic() + 5.0
        while len(calls) < 3 and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        env.close()
    assert len(calls) >= 3, "the timer thread died on the first failure"


def test_non_value_free_step_payload_is_recorded_as_failed(tmp_path: Path) -> None:
    from mas.lab.benchmark.environment.step import ActionResult

    env = _env(
        {"triggers": [{"id": "a", "when": {"on": "tool_call"}, "do": [{"type": "emit", "config": {"name": "a"}}]}]},
        tmp_path,
    )

    async def bad(spec, ctx):  # type: ignore[no-untyped-def]
        return ActionResult(payload={"note": "Hello World, this is free text"})

    import mas.lab.benchmark.environment.engine as engine

    original = engine._act
    engine._act = bad  # type: ignore[assignment]
    try:
        env.on_transition("tool_call", tool="t")
    finally:
        engine._act = original  # type: ignore[assignment]
        env.close()
    failed = [r for r in env.events.records() if r["name"] == "action.failed"]
    assert failed and failed[-1]["payload"].get("error") == "payload"


def test_events_left_in_the_inbox_are_logged_as_undelivered(tmp_path: Path) -> None:
    env = _env({}, tmp_path)
    env.deliver_event("late", {})
    env.close()
    rows = [r for r in env.events.records() if r["name"] == "event.delivered"]
    assert rows and rows[-1]["outcome"] == "skipped" and rows[-1]["payload"]["delivered"] == 0


@pytest.mark.parametrize(
    "raw",
    [
        {"limits": {"action_timeout_s": 0}},
        {"limits": {"max_sleep_s": -1}},
        {"limits": {"max_events_per_run": 0}},
        {"limits": {"action_timeout_s": float("nan")}},
        {"fixtures": [{"name": "f", "command": ["x"], "port": 70000}]},
        {"fixtures": [{"name": "f", "command": ["x"], "health": {"timeout_s": 0}}]},
        {"fixtures": [{"name": "f", "command": ["x"], "stop_grace_s": -2}]},
    ],
)
def test_nonsense_limits_and_ports_are_refused_at_compile(raw: dict) -> None:
    with pytest.raises(ValueError):
        compile_environment(raw)


def test_fixture_env_is_an_allowlist_not_the_host_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    from mas.lab.benchmark.environment.fixtures import _fixture_env

    monkeypatch.setenv("DATABASE_URL", "postgres://user:hunter2@db/prod")
    monkeypatch.setenv("GH_PAT", "ghp_x")
    monkeypatch.setenv("PLAIN_URL", "http://example.com/x")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    env = _fixture_env(compile_fixture({"name": "f", "command": ["x"]}), os.environ)
    assert "DATABASE_URL" not in env
    assert "GH_PAT" not in env
    assert "PLAIN_URL" not in env
    assert "OPENAI_API_KEY" not in env
    assert "PATH" in env


def test_fixture_probes_ignore_the_proxy_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    reg = FixtureRegistry(base_env=dict(os.environ), output_dir=tmp_path, work_dir=tmp_path)
    spec = compile_fixture(
        {
            "name": "web",
            "command": ["python3", "-m", "http.server", "{port}", "--bind", "127.0.0.1"],
            "port": "auto",
            "health": {"path": "/", "timeout_s": 10},
        }
    )
    try:
        reg.start(spec, scope_key="k")
        assert reg.url("web", scope_key="k").startswith("http://127.0.0.1:")
    finally:
        reg.close_all(force=True)


def test_minimal_env_mode_passes_only_the_basics(monkeypatch: pytest.MonkeyPatch) -> None:
    from mas.lab.benchmark.environment.fixtures import _fixture_env

    monkeypatch.setenv("SOME_ODD_NAME", "x")
    monkeypatch.setenv("KEEP_ME", "y")
    spec = compile_fixture({"name": "f", "command": ["x"], "env_mode": "minimal", "passthrough": ["KEEP_ME"]})
    env = _fixture_env(spec, os.environ)
    assert "SOME_ODD_NAME" not in env and env["KEEP_ME"] == "y" and "PATH" in env
    with pytest.raises(ValueError):
        compile_fixture({"name": "f", "command": ["x"], "env_mode": "loose"})


def test_watch_globs_join_the_cache_key(tmp_path: Path) -> None:
    from mas.lab.benchmark.environment.attach import fixture_file_digests

    (tmp_path / "helpers").mkdir()
    helper = tmp_path / "helpers" / "util.py"
    helper.write_text("A = 1\n", encoding="utf-8")
    spec = compile_environment(
        {"fixtures": [{"name": "f", "command": ["python3", "main.py"], "watch": ["helpers", "../outside*"]}]}
    )
    before = fixture_file_digests(spec, tmp_path)
    helper.write_text("A = 2\n", encoding="utf-8")
    after = fixture_file_digests(spec, tmp_path)
    assert list(before) == ["helpers/util.py"] and before != after


def test_events_reach_the_file_before_close(tmp_path: Path) -> None:
    path = tmp_path / "env.jsonl"
    env = RunEnvironment(compile_environment({}), run=RunCoordinates(), path=path, work_dir=tmp_path)
    env.emit_named("ping")
    assert "user.ping" in path.read_text(encoding="utf-8")
    env.close()
    assert path.read_text(encoding="utf-8").rstrip().endswith("}")


def test_fixture_folder_joins_the_cache_key(tmp_path: Path) -> None:
    from mas.lab.benchmark.environment.attach import fixture_file_digests

    (tmp_path / "fixtures").mkdir()
    (tmp_path / "fixtures" / "main.py").write_text("import helper\n", encoding="utf-8")
    helper = tmp_path / "fixtures" / "helper.py"
    helper.write_text("A = 1\n", encoding="utf-8")
    spec = compile_environment({"fixtures": [{"name": "f", "command": ["python3", "fixtures/main.py"]}]})
    before = fixture_file_digests(spec, tmp_path)
    helper.write_text("A = 2\n", encoding="utf-8")
    assert "fixtures/helper.py" in before and fixture_file_digests(spec, tmp_path) != before


def test_fixture_step_timeout_cancels_the_step(monkeypatch: pytest.MonkeyPatch) -> None:
    import mas.lab.benchmark.environment.fixtures as fx
    from mas.lab.benchmark.environment.step import ActionResult

    cancelled = threading.Event()

    class Slow:
        async def act(self, ctx):  # type: ignore[no-untyped-def]
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return ActionResult()

    monkeypatch.setattr("mas.lab.benchmark.environment.step.instantiate_step", lambda spec: Slow())
    real_join = threading.Thread.join

    def quick(self, timeout=None):  # type: ignore[no-untyped-def]
        return real_join(self, 0.3 if timeout == 30.0 else timeout)

    monkeypatch.setattr(threading.Thread, "join", quick)

    async def run() -> None:
        reg = FixtureRegistry(base_env=dict(os.environ), output_dir=Path("."), work_dir=Path("."))
        fx._run_fixture_step(object(), reg)

    with pytest.raises(TimeoutError):
        asyncio.run(run())
    assert cancelled.wait(3.0)


def test_the_library_never_reads_the_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from mas.lab.benchmark.environment.fixtures import _fixture_env

    monkeypatch.setenv("SOMETHING_ELSE", "x")
    env = _fixture_env(compile_fixture({"name": "f", "command": ["x"], "env": {"A": "1"}}), {"B": "2", "PATH": "/bin"})
    assert env["A"] == "1" and env["PATH"] == "/bin"
    assert "B" not in env
    assert "SOMETHING_ELSE" not in env


def test_the_controller_does_not_show_the_cli_environment(tmp_path: Path) -> None:
    from mas.lab.controller.registry import WorkerRegistry
    from mas.lab.controller.workers import run_benchmark_worker

    registry = WorkerRegistry()
    record = run_benchmark_worker(
        registry,
        type("R", (), {"submit": lambda self, *a, **k: None})(),
        {"experiment_yaml": str(tmp_path / "x.yaml"), "fixture_env": {"OPENAI_API_KEY": "sk-secret"}},
    )
    assert "fixture_env" not in record.spec


def test_hub_unbind_leaves_a_newer_binding_alone() -> None:
    from mas.lab.benchmark.environment.hub import get_environment_hub
    from mas.lab.benchmark.pipeline.keys import RunKey

    class Sink:
        def deliver_event(self, name, payload=None, *, source=None):  # type: ignore[no-untyped-def]
            return True

    hub, key = get_environment_hub(), RunKey("e", "s", "i", "r")
    old, new = Sink(), Sink()
    hub.bind(key, old)
    hub.bind(key, new)
    hub.unbind(key, old)
    assert hub.emit(key, "user.x")
    hub.unbind(key, new)
    assert not hub.emit(key, "user.x")
