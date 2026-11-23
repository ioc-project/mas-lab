#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
from __future__ import annotations

import pytest
from mas.lab.benchmark.environment.clock import RunClock
from mas.lab.benchmark.environment.model import ClockSpec, Limits
from mas.library.standard.lib.triggers import parse_duration


def test_virtual_advance_and_tick() -> None:
    clock = RunClock(ClockSpec(kind="virtual", start_s=0.0, tick={"tool_call": 0.5, "llm_call": 2.0}))
    assert clock.now() == 0.0
    clock.advance(1.0)
    assert clock.now() == 1.0
    clock.tick("llm_call")
    assert clock.now() == 3.0


def test_wall_refuses_advance() -> None:
    clock = RunClock(ClockSpec(kind="wall"))
    with pytest.raises(RuntimeError, match="virtual"):
        clock.advance(1.0)


def test_sleep_bounded() -> None:
    clock = RunClock(ClockSpec(kind="wall"))
    with pytest.raises(ValueError, match="max_sleep"):
        clock.sleep(120, bound_s=Limits().max_sleep_s)
    clock.sleep(0.01, bound_s=1.0)


def test_timers_due_in_order() -> None:
    from mas.lab.benchmark.environment.engine import RunEnvironment
    from mas.lab.benchmark.environment.model import RunCoordinates, compile_environment

    spec = compile_environment(
        {
            "clock": {"kind": "virtual"},
            "timers": [
                {"id": "late", "at": "5s", "do": [{"type": "emit", "config": {"name": "user.late"}}]},
                {"id": "early", "at": "1s", "do": [{"type": "emit", "config": {"name": "user.early"}}]},
            ],
        }
    )
    env = RunEnvironment(spec, run=RunCoordinates(), path=None)
    env.clock.advance(1.0)
    names = [r["name"] for r in env.events.records()]
    assert names.count("timer.fired") >= 1
    env.clock.advance(4.0)
    fired = [r for r in env.events.records() if r["name"] == "timer.fired"]
    assert [r["source"]["id"] for r in fired] == ["early", "late"]


def test_wall_timer_thread_fires() -> None:
    import time

    from mas.lab.benchmark.environment.engine import RunEnvironment
    from mas.lab.benchmark.environment.model import RunCoordinates, compile_environment

    spec = compile_environment(
        {
            "clock": {"kind": "wall"},
            "timers": [{"id": "soon", "at": "50ms", "do": [{"type": "emit", "config": {"name": "user.tick"}}]}],
        }
    )
    env = RunEnvironment(spec, run=RunCoordinates())
    try:
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            if any(r["name"] == "timer.fired" for r in env.events.records()):
                break
            time.sleep(0.05)
        assert any(r["name"] == "timer.fired" for r in env.events.records())
    finally:
        env.close()


def test_parse_duration() -> None:
    assert parse_duration("21s") == 21.0
    assert parse_duration("500ms") == 0.5
    assert parse_duration("2m") == 120.0
