# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""A2A receiver history belongs to its context, not the shared runtime instance."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from mas.ctl.session.exposure import make_runtime_handler
from mas.runtime.boundary.context.working_memory_registry import WorkingMemoryConfig
from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.engine.simulated import SimulatedEngine
from mas.runtime.schema.ingress import EngineIoReturn


def receiver(*, persistent=True, agent="worker"):
    engine = SimulatedEngine(llm_next_step=lambda _: "STOP")
    original = engine.invoke

    def invoke(io):
        if io.op == "LLM_CALL":
            return EngineIoReturn(
                correlation_id=io.correlation_id, response_kind="MODEL_TEXT", next_step="STOP", text="answer"
            )
        return original(io)

    engine.invoke = invoke
    instance = RuntimeInstance.from_parts(engine=engine)
    instance.working_memory = WorkingMemoryConfig(persistent=persistent)
    return instance, make_runtime_handler(instance, manifest={"metadata": {"name": agent}})


def contents(instance):
    return [message["content"] for message in instance.driver.ctx.committed_messages]


@pytest.mark.parametrize("persistent", [True, False])
def test_followup_and_separate_contexts(persistent):
    instance, handle = receiver(persistent=persistent)
    handle("first", session_id="one")
    handle("second", session_id="one")
    assert ("first" in contents(instance)) is persistent
    assert "second" in contents(instance)
    handle("unrelated", session_id="two")
    assert "first" not in contents(instance)
    assert "second" not in contents(instance)
    handle("third", session_id="one")
    assert ("second" in contents(instance)) is persistent
    assert "unrelated" not in contents(instance)


def test_receivers_do_not_share_history_even_with_identical_ids():
    first, first_handle = receiver()
    second, second_handle = receiver()
    first_handle("private", session_id="same")
    second_handle("other", session_id="same")
    assert "private" not in contents(second)
    assert "other" not in contents(first)


def test_failure_restores_last_committed_history(monkeypatch):
    instance, handle = receiver()
    handle("committed", session_id="one")
    original = instance.run_user_text

    def fail(*args, **kwargs):
        instance.driver.ctx.committed_messages.append({"role": "user", "content": "uncommitted"})
        raise RuntimeError("failed turn")

    monkeypatch.setattr(instance, "run_user_text", fail)
    with pytest.raises(RuntimeError, match="failed turn"):
        handle("fail", session_id="one")
    monkeypatch.setattr(instance, "run_user_text", original)
    handle("recovered", session_id="one")
    assert "committed" in contents(instance)
    assert "uncommitted" not in contents(instance)


def test_contexts_are_serialized_while_the_instance_runs(monkeypatch):
    instance, handle = receiver()
    entered, release, other_entered = Event(), Event(), Event()
    original = instance.run_user_text

    def run(text, **kwargs):
        if text == "first":
            entered.set()
            assert release.wait(5)
        else:
            other_entered.set()
        return original(text, **kwargs)

    monkeypatch.setattr(instance, "run_user_text", run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(handle, "first", session_id="one")
        assert entered.wait(5)
        second = pool.submit(handle, "second", session_id="two")
        try:
            assert not other_entered.wait(0.05)
        finally:
            release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    assert "first" not in contents(instance)
    assert "second" in contents(instance)


def test_protocol_text_is_not_interpreted_as_a_terminal_command():
    instance, handle = receiver()
    handle("/skills", session_id="one")
    assert "/skills" in contents(instance)


def test_tool_history_is_restored_without_replaying_execution(monkeypatch):
    instance, handle = receiver()
    original = instance.run_user_text
    observed = []
    executions = []

    def run(text, **kwargs):
        observed.append(list(instance.driver.ctx.committed_messages))
        trace = original(text, **kwargs)
        if text == "lookup":
            executions.append("lookup_record")
            instance.driver.ctx.working_memory.messages.extend(
                [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "lookup-1",
                                "type": "function",
                                "function": {"name": "lookup_record", "arguments": "{}"},
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": "lookup-1", "content": "record owner: Alex"},
                ]
            )
        return trace

    monkeypatch.setattr(instance, "run_user_text", run)
    handle("lookup", session_id="one")
    handle("follow up", session_id="one")
    assert any(m.get("role") == "tool" and m.get("content") == "record owner: Alex" for m in observed[-1])
    assert executions == ["lookup_record"]
