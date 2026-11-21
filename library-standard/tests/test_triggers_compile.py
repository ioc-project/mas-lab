#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
from __future__ import annotations

import pytest
from mas.library.standard.lib.triggers import (
    StepSpec,
    TriggerMatcher,
    When,
    compile_step,
    compile_timer,
    compile_trigger,
    compile_triggers,
    compile_when,
    parse_duration,
)


def test_parse_duration_units_and_error() -> None:
    assert parse_duration("500ms") == 0.5
    assert parse_duration("21s") == 21.0
    assert parse_duration("2m") == 120.0
    assert parse_duration("1h") == 3600.0
    with pytest.raises(ValueError, match="duration"):
        parse_duration("soon")


def test_compile_when_yaml11_on_key() -> None:
    when = compile_when({True: "tool_result", "tool": "pay_fare", "occurrence": 2})
    assert when.on == "tool_result" and when.tool == "pay_fare" and when.occurrence == 2


def test_compile_when_shapes_and_errors() -> None:
    already = When(on="timer", at_s=1.0)
    assert compile_when(already) is already
    from_line = compile_when("break tool_result pay_fare if 2")
    assert from_line.tool == "pay_fare" and from_line.occurrence == 2
    with pytest.raises(ValueError, match="mapping"):
        compile_when(3)
    with pytest.raises(ValueError, match="when.on"):
        compile_when({})
    with pytest.raises(ValueError, match="when.on"):
        compile_when({"on": "sneeze"})
    with pytest.raises(ValueError, match="match"):
        compile_when({"on": "event", "match": ["nope"]})
    when = compile_when(
        {
            "on": "timer",
            "at": "1s",
            "every": "2s",
            "delay": "500ms",
            "after": "gate",
            "occurrence": True,
            "event": "user.x",
            "match": {"kind": "span"},
        }
    )
    assert when.at_s == 1.0 and when.every_s == 2.0 and when.delay_s == 0.5
    assert when.occurrence == "every"
    assert compile_when({"on": "tool_call", "occurrence": None}).occurrence == "first"
    assert compile_when({"on": "tool_call", "occurrence": 3}).occurrence == 3
    assert compile_when({"on": "tool_call", "occurrence": "4"}).occurrence == 4
    with pytest.raises(ValueError, match="occurrence"):
        compile_when({"on": "tool_call", "occurrence": 0})
    with pytest.raises(ValueError, match="occurrence"):
        compile_when({"on": "tool_call", "occurrence": "nope"})
    with pytest.raises(ValueError, match="occurrence"):
        compile_when({"on": "tool_call", "occurrence": "0"})


def test_compile_step_and_trigger_errors() -> None:
    spec = StepSpec(type="sleep", config={"for": 1})
    assert compile_step(spec) is spec
    with pytest.raises(ValueError, match="mapping"):
        compile_step("sleep")
    with pytest.raises(ValueError, match="type"):
        compile_step({"config": {}})
    with pytest.raises(ValueError, match="config"):
        compile_step({"type": "sleep", "config": "nope"})
    with pytest.raises(ValueError, match="trigger id"):
        compile_trigger({"id": "NOPE", "when": {"on": "timer", "at": "1s"}})
    with pytest.raises(ValueError, match="do"):
        compile_trigger({"id": "t", "when": {"on": "timer", "at": "1s"}, "do": "http_call"})
    with pytest.raises(ValueError, match="max_fires"):
        compile_trigger({"id": "t", "when": {"on": "timer", "at": "1s"}, "max_fires": 0})
    every = compile_trigger({"id": "t", "when": {"on": "tool_call", "occurrence": "every"}})
    assert every.repeat is True and every.max_fires == 10_000


def test_compile_timer_and_triggers_list() -> None:
    timer = compile_timer({"id": "tick", "at": "1s", "every": "2s", "after": "start", "delay": "10ms"})
    assert timer.when.on == "timer"
    assert timer.when.at_s == 1.0 and timer.when.every_s == 2.0
    compiled = compile_triggers(
        triggers=[{"id": "a", "when": {"on": "event", "event": "user.x"}}],
        timers=[{"id": "b", "at": "1s"}],
    )
    assert [item.id for item in compiled] == ["a", "b"]
    with pytest.raises(ValueError, match="each trigger"):
        compile_triggers(triggers=["nope"])
    with pytest.raises(ValueError, match="each timer"):
        compile_triggers(timers=["nope"])
    with pytest.raises(ValueError, match="duplicate"):
        compile_triggers(
            triggers=[{"id": "a", "when": {"on": "timer", "at": "1s"}}, {"id": "a", "when": {"on": "event"}}]
        )


def test_timer_schedule_and_after_validation() -> None:
    with pytest.raises(ValueError, match="needs at"):
        compile_trigger({"id": "t", "when": {"on": "timer"}})
    with pytest.raises(ValueError, match="only apply to timers"):
        compile_trigger({"id": "t", "when": {"on": "tool_call", "delay": "1s"}})
    with pytest.raises(ValueError, match="every"):
        compile_timer({"id": "t", "every_s": 0})
    with pytest.raises(ValueError, match="unknown trigger"):
        compile_triggers(timers=[{"id": "t", "after": "ghost"}])
    assert compile_trigger({"id": "e", "when": {"on": "event", "event": "Alarm"}}).when.event == "user.alarm"
    every = compile_timer({"id": "t", "every": "5s"})
    assert every.repeat is True and every.max_fires == 10_000


def test_matcher_reports_a_spent_trigger_once_per_match() -> None:
    from mas.library.standard.lib.triggers import TriggerMatcher

    trigger = compile_trigger({"id": "t", "when": {"on": "tool_call", "tool": "pay"}})
    matcher = TriggerMatcher((trigger,))
    assert matcher.match(on="tool_call", tool="pay") == (trigger,)
    assert matcher.last_skipped == ()
    assert matcher.match(on="tool_call", tool="other") == ()
    assert matcher.last_skipped == ()
    assert matcher.match(on="tool_call", tool="pay") == ()
    assert matcher.last_skipped == (trigger,)


def test_matcher_event_observability_and_reset() -> None:
    event = compile_trigger({"id": "ev", "when": {"on": "event", "event": "user.alarm", "occurrence": "every"}})
    obs = compile_trigger(
        {
            "id": "obs",
            "when": {"on": "observability", "match": {"kind": "span"}, "occurrence": "every"},
        }
    )
    matcher = TriggerMatcher((event, obs))
    assert matcher.match(on="event", event="other") == ()
    assert [t.id for t in matcher.match(on="event", event="user.alarm")] == ["ev"]
    assert matcher.match(on="observability", observability={"kind": "log"}) == ()
    assert [t.id for t in matcher.match(on="observability", observability={"kind": "span"})] == ["obs"]
    assert matcher.count("ev") == 1
    assert matcher.fires("obs") == 1
    matcher.reset()
    assert matcher.count("ev") == 0
    assert matcher.record_fire("ev") == 1


@pytest.mark.parametrize("key", ["at_s", "every_s", "delay_s"])
def test_timer_times_must_be_finite(key: str) -> None:
    with pytest.raises(ValueError, match="finite"):
        compile_when({"on": "timer", key: float("nan")})
