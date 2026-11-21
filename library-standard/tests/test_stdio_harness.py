#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The stdio-harness runner: framing, the allow-list, the environment, deadlines, caps and teardown."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest
from mas.library.standard.plugins.runner.stdio_harness import StdioHarnessRunner, escape_line, unescape_line
from mas.runtime.boundary.runner import RunnerError, RunnerTask

STUB = str(Path(__file__).resolve().parents[2] / "tests" / "support" / "stub_harness.py")
PY = os.path.realpath(sys.executable)


def make(framing: str = "line", **kwargs) -> StdioHarnessRunner:
    kwargs.setdefault("timeout", 10)
    return StdioHarnessRunner([PY, STUB, framing], [PY], framing=framing, **kwargs)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_escape_round_trips_newlines_and_backslashes() -> None:
    text = "a\nb\\nc\r\n\\"
    assert "\n" not in escape_line(text)
    assert unescape_line(escape_line(text)) == text


def test_line_framing_carries_a_multiline_task() -> None:
    reply = make().run(RunnerTask(text="first\nsecond", session_id="s1"))
    assert reply.text == "echo:first\nsecond"
    assert reply.context_id == "s1"


def test_jsonrpc_framing_returns_events_and_the_session_id() -> None:
    reply = make("jsonrpc").run(RunnerTask(text="hi", session_id="s1", task_id="t1", caller="alice"))
    assert reply.text == "echo:hi"
    assert reply.context_id == "s1"
    assert reply.events == ({"kind": "tool_call", "name": "grep", "task": "hi"},)


def test_command_must_be_allow_listed_absolute_and_not_a_shell_string() -> None:
    with pytest.raises(ValueError, match="not in allow"):
        StdioHarnessRunner([PY, STUB, "line"], ["/bin/true"])
    with pytest.raises(ValueError, match="absolute"):
        StdioHarnessRunner(["python3", STUB, "line"], ["python3"])
    with pytest.raises(ValueError, match="not a shell string"):
        StdioHarnessRunner(f"{PY} {STUB} line", [PY])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="allow"):
        StdioHarnessRunner([PY, STUB, "line"], [])
    with pytest.raises(ValueError, match="framing"):
        make("xml")


def test_the_child_gets_only_the_environment_it_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "do-not-leak")
    monkeypatch.setenv("HOME", "/somewhere")
    # the interpreter and the OS add a few variables of their own (locale); nothing of ours comes along
    bare = make().run(RunnerTask(text="ENV")).text.split(",")
    assert not {"AWS_SECRET_ACCESS_KEY", "HOME", "PATH"} & set(bare)
    given = make(env={"ONLY": "this"}).run(RunnerTask(text="ENV")).text.split(",")
    assert "ONLY" in given
    assert not {"AWS_SECRET_ACCESS_KEY", "HOME", "PATH"} & set(given)


def test_a_task_with_no_reply_times_out_and_the_process_is_killed() -> None:
    runner = make(timeout=0.5, term_grace=0.2)
    started = time.monotonic()
    with pytest.raises(RunnerError, match="no reply within"):
        runner.run(RunnerTask(text="SLEEP"))
    assert time.monotonic() - started < 5


def test_per_task_process_is_gone_after_the_reply() -> None:
    pid = int(make().run(RunnerTask(text="PID")).text)
    deadline = time.monotonic() + 5
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not alive(pid)


def test_size_caps_refuse_a_big_reply_and_a_big_task() -> None:
    with pytest.raises(RunnerError, match="line over|byte cap"):
        make(max_reply_bytes=1000).run(RunnerTask(text="BIG"))
    with pytest.raises(RunnerError, match="task is"):
        make(max_task_bytes=10).run(RunnerTask(text="x" * 50))


def test_the_reply_cap_counts_all_the_output_of_one_task_not_only_one_line() -> None:
    with pytest.raises(RunnerError, match="reply is over"):
        make("jsonrpc", max_reply_bytes=5000).run(RunnerTask(text="MANY"))
    assert len(make("jsonrpc").run(RunnerTask(text="MANY")).events) == 200


def test_a_harness_that_exits_without_a_reply_is_a_runner_error() -> None:
    with pytest.raises(RunnerError, match="exit status 3"):
        make().run(RunnerTask(text="DIE"))


def test_a_jsonrpc_line_that_is_not_json_is_a_runner_error() -> None:
    with pytest.raises(RunnerError, match="not JSON"):
        make("jsonrpc").run(RunnerTask(text="GARBAGE"))


def test_persistent_keeps_one_process_respawns_after_death_and_close_ends_it() -> None:
    runner = make(lifecycle="persistent")
    first = int(runner.run(RunnerTask(text="PID")).text)
    assert int(runner.run(RunnerTask(text="PID")).text) == first
    with pytest.raises(RunnerError):
        runner.run(RunnerTask(text="DIE"))
    second = int(runner.run(RunnerTask(text="PID")).text)
    assert second != first
    runner.close()
    runner.close()
    deadline = time.monotonic() + 5
    while alive(second) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not alive(second)
    with pytest.raises(RunnerError, match="closed"):
        runner.run(RunnerTask(text="late"))
