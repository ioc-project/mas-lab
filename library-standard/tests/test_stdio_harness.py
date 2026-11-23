#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The stdio-harness loop: framing, the allow-list, the environment, deadlines, caps and teardown."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest
from mas.library.standard.plugins.loop.stdio_harness import StdioHarnessLoop, escape_line, unescape_line
from mas.runtime.boundary.loop import LoopError, LoopTask

STUB = str(Path(__file__).resolve().parents[2] / "tests" / "support" / "stub_harness.py")
PY = os.path.realpath(sys.executable)


def make(framing: str = "line", **kwargs) -> StdioHarnessLoop:
    kwargs.setdefault("timeout", 10)
    return StdioHarnessLoop([PY, STUB, framing], [PY], framing=framing, **kwargs)


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
    reply = make().run(LoopTask(text="first\nsecond", session_id="s1"))
    assert reply.text == "echo:first\nsecond"
    assert reply.context_id == "s1"


def test_jsonrpc_framing_returns_events_and_the_session_id() -> None:
    reply = make("jsonrpc").run(LoopTask(text="hi", session_id="s1", task_id="t1", caller="alice"))
    assert reply.text == "echo:hi"
    assert reply.context_id == "s1"
    assert reply.events == ({"kind": "tool_call", "name": "grep", "task": "hi"},)


def test_command_must_be_allow_listed_absolute_and_not_a_shell_string() -> None:
    with pytest.raises(ValueError, match="not in allow"):
        StdioHarnessLoop([PY, STUB, "line"], ["/bin/true"])
    with pytest.raises(ValueError, match="absolute"):
        StdioHarnessLoop(["python3", STUB, "line"], ["python3"])
    with pytest.raises(ValueError, match="not a shell string"):
        StdioHarnessLoop(f"{PY} {STUB} line", [PY])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="allow"):
        StdioHarnessLoop([PY, STUB, "line"], [])
    with pytest.raises(ValueError, match="framing"):
        make("xml")


def test_the_child_gets_only_the_environment_it_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "do-not-leak")
    monkeypatch.setenv("HOME", "/somewhere")
    # the interpreter and the OS add a few variables of their own (locale); nothing of ours comes along
    bare = make().run(LoopTask(text="ENV")).text.split(",")
    assert not {"AWS_SECRET_ACCESS_KEY", "HOME", "PATH"} & set(bare)
    given = make(env={"ONLY": "this"}).run(LoopTask(text="ENV")).text.split(",")
    assert "ONLY" in given
    assert not {"AWS_SECRET_ACCESS_KEY", "HOME", "PATH"} & set(given)


def test_a_task_with_no_reply_times_out_and_the_process_is_killed() -> None:
    loop = make(timeout=0.5, term_grace=0.2)
    started = time.monotonic()
    with pytest.raises(LoopError, match="no reply within"):
        loop.run(LoopTask(text="SLEEP"))
    assert time.monotonic() - started < 5


def test_per_task_process_is_gone_after_the_reply() -> None:
    pid = int(make().run(LoopTask(text="PID")).text)
    deadline = time.monotonic() + 5
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not alive(pid)


def test_size_caps_refuse_a_big_reply_and_a_big_task() -> None:
    with pytest.raises(LoopError, match="line over|byte cap"):
        make(max_reply_bytes=1000).run(LoopTask(text="BIG"))
    with pytest.raises(LoopError, match="task is"):
        make(max_task_bytes=10).run(LoopTask(text="x" * 50))


def test_the_reply_cap_counts_all_the_output_of_one_task_not_only_one_line() -> None:
    with pytest.raises(LoopError, match="reply is over"):
        make("jsonrpc", max_reply_bytes=5000).run(LoopTask(text="MANY"))
    assert len(make("jsonrpc").run(LoopTask(text="MANY")).events) == 200


def test_a_harness_that_exits_without_a_reply_is_a_loop_error() -> None:
    with pytest.raises(LoopError, match="exit status 3"):
        make().run(LoopTask(text="DIE"))


def test_a_jsonrpc_line_that_is_not_json_is_a_loop_error() -> None:
    with pytest.raises(LoopError, match="not JSON"):
        make("jsonrpc").run(LoopTask(text="GARBAGE"))


def test_persistent_keeps_one_process_respawns_after_death_and_close_ends_it() -> None:
    loop = make(lifecycle="persistent")
    first = int(loop.run(LoopTask(text="PID")).text)
    assert int(loop.run(LoopTask(text="PID")).text) == first
    with pytest.raises(LoopError):
        loop.run(LoopTask(text="DIE"))
    second = int(loop.run(LoopTask(text="PID")).text)
    assert second != first
    loop.close()
    loop.close()
    deadline = time.monotonic() + 5
    while alive(second) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not alive(second)
    with pytest.raises(LoopError, match="closed"):
        loop.run(LoopTask(text="late"))
