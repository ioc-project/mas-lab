#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``new_thread`` starts a thread in an empty context on every build, with no process setting."""

from __future__ import annotations

import contextvars
import subprocess
import sys
import textwrap
import threading

from mas.runtime.aio import new_thread

_VAR: contextvars.ContextVar[str] = contextvars.ContextVar("test_new_thread_var", default="unset")


def test_the_thread_does_not_see_the_callers_context_variables() -> None:
    _VAR.set("caller")
    seen: list[str] = []
    thread = new_thread(target=lambda: seen.append(_VAR.get()))
    thread.start()
    thread.join()
    assert seen == ["unset"]
    assert _VAR.get() == "caller"


def test_what_the_thread_sets_stays_in_the_thread() -> None:
    _VAR.set("caller")
    thread = new_thread(target=lambda: _VAR.set("thread"))
    thread.start()
    thread.join()
    assert _VAR.get() == "caller"


def test_arguments_name_and_daemon_are_passed_through() -> None:
    got: list[tuple[int, int]] = []
    thread = new_thread(target=lambda a, b: got.append((a, b)), args=(1, 2), name="named", daemon=True)
    assert isinstance(thread, threading.Thread)
    assert thread.name == "named" and thread.daemon is True
    thread.start()
    thread.join()
    assert got == [(1, 2)]


def test_keyword_arguments_are_passed_to_the_target() -> None:
    got: list[dict[str, int]] = []
    thread = new_thread(target=lambda **kw: got.append(kw), kwargs={"a": 1, "b": 2})
    thread.start()
    thread.join()
    assert got == [{"a": 1, "b": 2}]


def test_positional_and_keyword_arguments_combine() -> None:
    got: list[tuple] = []
    thread = new_thread(target=lambda x, *, y: got.append((x, y)), args=(1,), kwargs={"y": 2})
    thread.start()
    thread.join()
    assert got == [(1, 2)]


def test_an_error_in_the_target_reaches_the_thread_excepthook(monkeypatch) -> None:
    seen: list[BaseException] = []
    monkeypatch.setattr(threading, "excepthook", lambda args: seen.append(args.exc_value))

    def boom() -> None:
        raise RuntimeError("boom")

    thread = new_thread(target=boom)
    thread.start()
    thread.join()
    assert [str(e) for e in seen] == ["boom"]


def test_the_thread_is_not_started_by_new_thread() -> None:
    thread = new_thread(target=lambda: None)
    assert not thread.is_alive() and thread.ident is None


def test_it_stays_clean_when_the_interpreter_makes_threads_inherit_the_context() -> None:
    """3.14 can start threads with a copy of the caller's context (the default on the free-threaded build)."""
    code = textwrap.dedent(
        """
        import contextvars, sys, threading
        from mas.runtime.aio import new_thread

        var = contextvars.ContextVar("v", default="unset")
        var.set("caller")
        seen = {}

        def record(key):
            seen[key] = var.get()

        for key, make in (("plain", threading.Thread), ("clean", new_thread)):
            t = make(target=record, args=(key,))
            t.start()
            t.join()
        print(seen["plain"], seen["clean"])
        """
    )
    result = subprocess.run(
        [sys.executable, "-X", "thread_inherit_context=1", "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    plain, clean = result.stdout.split()
    assert clean == "unset"
    if hasattr(sys.flags, "thread_inherit_context"):
        assert plain == "caller"  # the flag took effect, so the clean thread was a real test
