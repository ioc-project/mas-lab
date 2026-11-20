#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
from __future__ import annotations

from mas.lab.benchmark.environment.engine import RunEnvironment
from mas.lab.benchmark.environment.hub import EnvironmentHub
from mas.lab.benchmark.environment.model import RunCoordinates, compile_environment
from mas.lab.benchmark.pipeline.keys import run_key_from_parts


def test_pipeline_event_triggers_kill_switch_at_next_boundary() -> None:
    spec = compile_environment(
        {
            "triggers": [
                {
                    "id": "alarm",
                    "when": {"on": "event", "event": "user.alarm"},
                    "do": [{"type": "kill_switch", "config": {}}],
                }
            ]
        }
    )
    env = RunEnvironment(spec, run=RunCoordinates())
    key = run_key_from_parts(experiment="e", scenario="s", item="item1", run="r1")
    hub = EnvironmentHub()
    hub.bind(key, env)
    delivered = hub.emit(key, "user.alarm", {"status": 1})
    assert delivered is True

    class A:
        hook = "ingress"
        op = "TOOL_RESULT"
        tool = ""
        agent = ""
        occurrence = 1

    env.on_transition("tool_result", anchor=A())
    names = [r["name"] for r in env.events.records()]
    assert "event.emitted" in names
    assert "event.delivered" in names
    assert "trigger.matched" in names
    hub.unbind(key)


def test_recorded_replay_redelivers_at_same_anchor(tmp_path) -> None:
    path = tmp_path / "environment.jsonl"
    spec = compile_environment(
        {
            "replay": "live",
            "triggers": [
                {
                    "id": "alarm",
                    "when": {"on": "event", "event": "user.alarm"},
                    "do": [{"type": "emit", "config": {"name": "user.saw"}}],
                }
            ],
        }
    )
    first = RunEnvironment(spec, run=RunCoordinates(), path=path)
    key = run_key_from_parts(experiment="e", scenario="s", item="item1", run="r1")
    hub = EnvironmentHub()
    hub.bind(key, first)
    first.deliver_event("user.alarm", {"status": 1})

    class A:
        hook = "ingress"
        op = "TOOL_RESULT"
        tool = ""
        agent = ""
        occurrence = 2

    first.on_transition("tool_result", anchor=A())
    first.close()
    hub.unbind(key)

    recorded = compile_environment(
        {
            "replay": "recorded",
            "triggers": [
                {
                    "id": "alarm",
                    "when": {"on": "event", "event": "user.alarm"},
                    "do": [{"type": "emit", "config": {"name": "user.saw"}}],
                }
            ],
        }
    )
    second = RunEnvironment(recorded, run=RunCoordinates(), path=path)
    second.on_transition("tool_result", anchor=A())
    names = [r["name"] for r in second.events.records()]
    assert "event.delivered" in names
    second.close()


def test_execution_context_emit_event_uses_run_key_or_parts() -> None:
    from mas.lab.benchmark.environment.hub import get_environment_hub
    from mas.lab.benchmark.pipeline.executor import ExecutionContext
    from mas.lab.benchmark.pipeline.resources import ScopeContext

    key = run_key_from_parts(experiment="e", scenario="s", item="item1", run="r1")
    hub = get_environment_hub()
    env = RunEnvironment(compile_environment({}), run=RunCoordinates())
    hub.bind(key, env)
    ctx = ExecutionContext.__new__(ExecutionContext)
    ctx.template_vars = {"run_key": key}
    ctx.scope_context = ScopeContext()
    ctx.lab_name = ""
    before = len(env.events.records())
    ctx.emit_event("user.from_key", {"status": 1})
    other = ExecutionContext.__new__(ExecutionContext)
    other.template_vars = {
        "experiment": "e",
        "scenario": "s",
        "test": "item1",
        "run": "r1",
    }
    other.scope_context = ScopeContext()
    other.lab_name = ""
    other.emit_event("user.from_parts")
    assert len(env.events.records()) > before
    hub.unbind(key)
