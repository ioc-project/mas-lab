#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-slim``: two commands, over the in-process fake node."""

from __future__ import annotations

import io
import threading
from pathlib import Path

import pytest
import slim_node_fake as slim_fake
from library_ioa.plugins.slim.actor import ConnectionActor
from library_ioa.plugins.slim.cli import main

_SECRET = "e4aaecb9ae0b23b82086bb8a8633e01fba16ae8d9c1379a613c00838"
_NS = ["--identity", "acme/lab/desk"]


def _stdin(text: str = _SECRET) -> io.StringIO:
    return io.StringIO(text)


@pytest.fixture(autouse=True)
def _fake_node(monkeypatch: pytest.MonkeyPatch) -> None:
    slim_fake.FakeWorld.reset()
    monkeypatch.setattr("library_ioa.plugins.slim.actor.load_slim_bindings", lambda: slim_fake)
    monkeypatch.setattr(ConnectionActor, "SUBSCRIBE_SETTLE_S", 0.0)
    monkeypatch.setattr(ConnectionActor, "ROUTE_SETTLE_S", 0.0)


def _listen(extra: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[threading.Thread, threading.Event]:
    stop = threading.Event()
    thread = threading.Thread(
        target=main, args=(["listen", *_NS, "--agent", "billing", *extra],), kwargs={"stop": stop, "stdin": _stdin()}
    )
    thread.start()
    for _ in range(100):
        if "is listening" in capsys.readouterr().out:
            return thread, stop
        stop.wait(0.05)
    stop.set()
    raise AssertionError("listen did not start")


def test_send_gets_the_listeners_reply(capsys: pytest.CaptureFixture[str]) -> None:
    thread, stop = _listen([], capsys)
    try:
        assert main(["send", *_NS, "--as", "triage", "billing", "I was charged twice"], stdin=_stdin()) == 0
        assert capsys.readouterr().out.strip().splitlines()[-1] == "ack: I was charged twice"
    finally:
        stop.set()
        thread.join(5)


def test_listen_prints_each_request_and_uses_a_fixed_reply(capsys: pytest.CaptureFixture[str]) -> None:
    thread, stop = _listen(["--reply", "see your invoice"], capsys)
    try:
        assert main(["send", *_NS, "--as", "triage", "billing", "refund?"], stdin=_stdin()) == 0
        out = capsys.readouterr().out
        assert "[billing] refund?" in out
        assert "see your invoice" in out
    finally:
        stop.set()
        thread.join(5)


def test_send_to_nobody_exits_one(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["send", *_NS, "--as", "triage", "nobody", "hello"], stdin=_stdin()) == 1
    assert "mas-slim:" in capsys.readouterr().err


def test_an_empty_secret_exits_one(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["send", *_NS, "--as", "triage", "billing", "hi"], stdin=_stdin("")) == 1
    assert "no secret on --secret-file" in capsys.readouterr().err


def test_the_secret_can_come_from_a_file_and_the_environment_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SLIM_SECRET", _SECRET)
    assert main(["send", *_NS, "--as", "triage", "billing", "hi"], stdin=_stdin("")) == 1  # the env var is ignored
    missing = tmp_path / "missing"
    assert main(["send", *_NS, "--secret-file", str(missing), "--as", "triage", "billing", "hi"]) == 1
    assert "cannot read the secret" in capsys.readouterr().err
    thread, stop = _listen([], capsys)
    try:
        path = tmp_path / "secret"
        path.write_text(_SECRET + "\n", encoding="utf-8")
        assert main(["send", *_NS, "--secret-file", str(path), "--as", "triage", "billing", "hi"]) == 0
        assert capsys.readouterr().out.strip().splitlines()[-1] == "ack: hi"
    finally:
        stop.set()
        thread.join(5)


def test_listen_stops_on_keyboard_interrupt(capsys: pytest.CaptureFixture[str]) -> None:
    class Boom(threading.Event):
        def wait(self, timeout: float | None = None) -> bool:
            del timeout
            raise KeyboardInterrupt

    assert main(["listen", *_NS, "--agent", "billing"], stop=Boom(), stdin=_stdin()) == 0
    assert "is listening" in capsys.readouterr().out


def test_timeout_bounds_the_wait_for_a_reply_too() -> None:
    from library_ioa.plugins.slim import cli

    args = cli._parser().parse_args(["send", "--identity", "acme/lab/desk", "--as", "a", "--timeout", "7", "b", "x"])
    stack = cli._stack(args, lambda *a, **k: "ok", _stdin())
    stack.start()
    try:
        actor = stack._services[0].actor
        assert actor.timeout_s == 7.0 and actor.reply_timeout_s == 7.0
    finally:
        stack.close()
