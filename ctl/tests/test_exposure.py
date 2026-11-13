#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Hosted agent_expose listeners for serve / chat / tui / run-mas."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from mas.ctl.compose.models import ResolvedInfra
from mas.ctl.compose.runner import ComposeResult
from mas.ctl.session.exposure import (
    close_exposures,
    expose_agent,
    exposure_targets,
    make_runtime_handler,
    start_hosted_exposures,
    start_materialized_exposures,
)


def test_exposure_targets_conversation_owner_and_local_deploy() -> None:
    assert exposure_targets(
        ["assistant", "greeter"],
        {
            "assistant": {
                "protocol": "a2a",
                "usage": "deploy",
                "url": "http://127.0.0.1:9001",
            },
            "greeter": {
                "protocol": "a2a",
                "usage": "deploy",
                "url": "http://127.0.0.1:9002",
            },
        },
        conversation_ids=["assistant"],
    ) == ["assistant", "greeter"]


def test_exposure_targets_chat_use_and_deploy_binds() -> None:
    assert exposure_targets(
        ["assistant"],
        {
            "assistant": {
                "protocol": "a2a",
                "usage": "use-and-deploy",
                "url": "http://127.0.0.1:9001",
            }
        },
        conversation_ids=["assistant"],
    ) == ["assistant"]


def test_exposure_targets_binds_hosted_use_and_deploy_specialists() -> None:
    assert exposure_targets(
        ["assistant", "greeter"],
        {
            "assistant": {
                "protocol": "a2a",
                "usage": "use-and-deploy",
                "url": "http://127.0.0.1:9001",
            },
            "greeter": {
                "protocol": "a2a",
                "usage": "use-and-deploy",
                "url": "http://127.0.0.1:9002",
            },
        },
        conversation_ids=["assistant"],
    ) == ["assistant", "greeter"]


def test_exposure_targets_skips_use_only_remote_peer() -> None:
    assert (
        exposure_targets(
            ["moderator", "schedule_agent"],
            {
                "schedule_agent": {
                    "protocol": "a2a",
                    "usage": "use",
                    "url": "http://127.0.0.1:9006",
                }
            },
            conversation_ids=["moderator"],
        )
        == []
    )


def test_exposure_targets_skips_use_only_and_missing_protocol() -> None:
    assert (
        exposure_targets(
            ["assistant", "oracle", "weather"],
            {
                "assistant": {"protocol": "a2a", "usage": "use", "url": "http://127.0.0.1:9001"},
                "oracle": {"url": "http://127.0.0.1:9005"},
                "weather": {"protocol": "mcp", "usage": "deploy", "url": "http://127.0.0.1:9001/mcp"},
            },
            conversation_ids=["assistant"],
        )
        == []
    )


def _materialized(applications: dict, agent_ids: list[str]) -> SimpleNamespace:
    instances = {aid: SimpleNamespace(driver=SimpleNamespace()) for aid in agent_ids}
    compose = ComposeResult(
        mas_id="demo",
        mas_config={"spec": {"workflow": {"entry": agent_ids[0]}}},
        effective_bind={},
        placement_plan={},
        deployment={},
        infra_refs=[],
        bind=MagicMock(agents=[]),
        plan=MagicMock(),
        resolved_infra=ResolvedInfra(applications=applications),
    )
    return SimpleNamespace(
        compose=compose,
        materialized=SimpleNamespace(instances=instances),
        mas_base_dir=".",
    )


def test_start_materialized_exposures_serves_entry_and_local_deploy_peers() -> None:
    materialized = _materialized(
        {
            "assistant": {
                "protocol": "a2a",
                "usage": "deploy",
                "url": "http://127.0.0.1:9001",
            },
            "greeter": {
                "protocol": "a2a",
                "usage": "deploy",
                "url": "http://127.0.0.1:9002",
            },
            "remote": {
                "protocol": "a2a",
                "usage": "use",
                "url": "http://127.0.0.1:9006",
            },
        },
        ["assistant", "greeter", "remote"],
    )
    created: list[str] = []
    handles: list[MagicMock] = []

    def _create(spec_key: str, binding=None, **kwargs):
        assert spec_key == "agent_expose"
        created.append(kwargs["endpoint"]["url"])
        handle = MagicMock()
        handle.host = "127.0.0.1"
        handle.port = int(kwargs["endpoint"]["url"].rsplit(":", 1)[1])
        handle.server = SimpleNamespace(thread=SimpleNamespace(is_alive=lambda: True))
        exposure = MagicMock()
        exposure.serve_background.return_value = handle
        handles.append(handle)
        return exposure

    with patch("mas.runtime.registry.get_registry", return_value=SimpleNamespace(create=_create)):
        with patch("mas.ctl.session.exposure._wait_listening"):
            started = start_materialized_exposures(
                materialized,
                entry_id="assistant",
                manifests={"assistant": {"metadata": {"name": "assistant"}}},
            )

    assert created == ["http://127.0.0.1:9001", "http://127.0.0.1:9002"]
    assert [item.listener for item in started] == handles
    close_exposures(started)
    for handle in handles:
        handle.close.assert_called_once()


def test_start_hosted_exposures_is_the_chat_path() -> None:
    instance = SimpleNamespace(driver=SimpleNamespace())
    handle = MagicMock()
    handle.host = "127.0.0.1"
    handle.port = 9001
    handle.server = SimpleNamespace(thread=SimpleNamespace(is_alive=lambda: True))
    exposure = MagicMock()
    exposure.serve_background.return_value = handle
    endpoint = {"protocol": "a2a", "usage": "deploy", "url": "http://127.0.0.1:9001"}

    with patch("mas.runtime.registry.get_registry", return_value=SimpleNamespace(create=lambda *a, **k: exposure)):
        with patch("mas.ctl.session.exposure._wait_listening"):
            started = start_hosted_exposures(
                {"solo": instance},
                {"solo": endpoint},
                conversation_ids=["solo"],
                manifests={"solo": {"metadata": {"name": "solo"}}},
            )

    assert [item.listener for item in started] == [handle]
    exposure.serve_background.assert_called_once()
    close_exposures(started)


def test_expose_agent_blocking_is_the_serve_path() -> None:
    instance = SimpleNamespace(driver=SimpleNamespace())
    exposure = MagicMock()
    endpoint = {"protocol": "a2a", "usage": "deploy", "url": "http://127.0.0.1:9005"}

    with patch("mas.runtime.registry.get_registry", return_value=SimpleNamespace(create=lambda *a, **k: exposure)):
        assert (
            expose_agent(
                instance,
                {"metadata": {"name": "qa-agent"}},
                endpoint,
                blocking=True,
            )
            is None
        )

    exposure.serve_blocking.assert_called_once()
    exposure.serve_background.assert_not_called()


def test_start_hosted_exposures_closes_on_listen_failure() -> None:
    handle = MagicMock()
    handle.host = "127.0.0.1"
    handle.port = 9001
    handle.server = SimpleNamespace(thread=SimpleNamespace(is_alive=lambda: True))
    exposure = MagicMock()
    exposure.serve_background.return_value = handle
    endpoint = {"protocol": "a2a", "usage": "deploy", "url": "http://127.0.0.1:9001"}

    with patch("mas.runtime.registry.get_registry", return_value=SimpleNamespace(create=lambda *a, **k: exposure)):
        with patch("mas.ctl.session.exposure._wait_listening", side_effect=RuntimeError("bind failed")):
            with pytest.raises(RuntimeError, match="bind failed"):
                start_hosted_exposures(
                    {"assistant": SimpleNamespace()},
                    {"assistant": endpoint},
                    conversation_ids=["assistant"],
                )

    handle.close.assert_called_once()


def _canned_instance(trace=None, calls=None):
    """A real instance whose engine run returns ``trace`` and records its arguments."""
    from mas.runtime.driver.driver import DriverTrace

    instance = _stop_instance()
    instance.conversation_factory = None
    original = instance.run_user_text

    def run_user_text(text, **kwargs):
        if calls is not None:
            calls.append((text, kwargs))
        return trace if trace is not None else original(text, **kwargs)

    instance.run_user_text = run_user_text  # type: ignore[method-assign]
    return instance, DriverTrace


def test_runtime_handler_forwards_turn_and_session_ids() -> None:
    calls: list[tuple[str, dict]] = []
    instance, _ = _canned_instance(calls=calls)
    handler = make_runtime_handler(instance)
    result = handler(
        "question",
        turn_id="turn-1",
        session_id="session-1",
        parent_call_id="parent-1",
        upstream_correlation_id=42,
    )
    assert result["context_id"] == "session-1"
    (text, kwargs) = calls[0]
    assert text == "question"
    assert (kwargs["turn_id"], kwargs["session_id"], kwargs["parent_call_id"], kwargs["upstream_correlation_id"]) == (
        "turn-1",
        "session-1",
        "parent-1",
        42,
    )


def test_runtime_handler_preserves_structured_artifacts() -> None:
    _, trace_type = _canned_instance()
    trace = trace_type()
    trace.client_responses = [
        SimpleNamespace(
            content="Generated output",
            finish_reason="stop",
            artifacts=({"kind": "data", "name": "result", "data": {"value": 42}},),
        )
    ]
    instance, _ = _canned_instance(trace)
    result = make_runtime_handler(instance)("question")
    assert result["text"] == "Generated output"
    assert result["context_id"]
    assert result["artifacts"] == [{"kind": "data", "name": "result", "data": {"value": 42}}]


def test_runtime_handler_marks_pending_hitl_as_input_required() -> None:
    _, trace_type = _canned_instance()
    trace = trace_type()
    trace.awaiting_hitl = True
    instance, _ = _canned_instance(trace)
    result = make_runtime_handler(instance)("approve this tool call")
    assert result["text"] == ""
    assert result["task_state"] == "input_required"
    assert result["context_id"]


def _stop_instance():
    from mas.runtime.driver.instance import RuntimeInstance
    from mas.runtime.engine.simulated import SimulatedEngine
    from mas.runtime.kernel.config import KernelConfig

    engine = SimulatedEngine(llm_next_step=lambda _cid: "STOP", stop_text="ok")

    def invoke(io):
        time.sleep(0.02)
        return SimulatedEngine.invoke(engine, io)

    engine.invoke = invoke  # type: ignore[method-assign]
    instance = RuntimeInstance.from_parts(engine=engine, config=KernelConfig())
    instance.capture_session_baseline()
    instance.conversation_factory = _stop_instance
    return instance


def test_a2a_omitted_context_id_starts_a_new_session() -> None:
    handler = make_runtime_handler(_stop_instance())
    first = handler("hello")
    second = handler("hello again")
    assert first["context_id"]
    assert second["context_id"]
    assert first["context_id"] != second["context_id"]


def test_a2a_same_context_id_reuses_the_session() -> None:
    handler = make_runtime_handler(_stop_instance())
    first = handler("hello", session_id="alice")
    second = handler("follow up", session_id="alice")
    assert first["context_id"] == "alice"
    assert second["context_id"] == "alice"
    session = handler.manager.get("alice")
    assert handler.manager.get("alice") is session
    assert session.instance.driver.ctx.turn_index == 2
    assert [user for user, _ in session.instance.driver.ctx.turn_history] == ["hello", "follow up"]


def test_a2a_two_context_ids_keep_separate_runtimes_and_histories() -> None:
    handler = make_runtime_handler(_stop_instance())
    problems: list[str] = []

    def run(session_id: str, text: str) -> None:
        result = handler(text, session_id=session_id)
        if result["context_id"] != session_id:
            problems.append(f"{session_id}: {result['context_id']!r}")

    threads = [
        threading.Thread(target=run, args=("alice", "from alice")),
        threading.Thread(target=run, args=("bob", "from bob")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert problems == []

    alice = handler.manager.get("alice")
    bob = handler.manager.get("bob")
    assert alice.instance is not bob.instance
    assert alice.instance.kernel is not bob.instance.kernel
    assert alice.instance.driver.ctx is not bob.instance.driver.ctx
    assert alice.instance.driver.ctx.turn_index == 1
    assert bob.instance.driver.ctx.turn_index == 1
    assert [user for user, _ in alice.instance.driver.ctx.turn_history] == ["from alice"]
    assert [user for user, _ in bob.instance.driver.ctx.turn_history] == ["from bob"]


def test_a2a_first_messages_on_one_context_id_open_one_conversation() -> None:
    handler = make_runtime_handler(_stop_instance())
    threads = [threading.Thread(target=handler, args=("hi",), kwargs={"session_id": "alice"}) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert list(handler.manager.sessions) == ["alice"]
    assert handler.manager.get("alice").instance.driver.ctx.turn_index == 8


def test_a2a_each_conversation_gets_its_own_engine_and_they_run_together() -> None:
    inside = 0
    overlap = 0
    guard = threading.Lock()

    def build():
        instance = _stop_instance()
        inner = instance.driver.engine.invoke

        def watched(io):
            nonlocal inside, overlap
            with guard:
                inside += 1
                overlap = max(overlap, inside)
            try:
                return inner(io)
            finally:
                with guard:
                    inside -= 1

        instance.driver.engine.invoke = watched  # type: ignore[method-assign]
        instance.conversation_factory = build
        return instance

    handler = make_runtime_handler(build())
    threads = [threading.Thread(target=handler, args=("hi",), kwargs={"session_id": f"c{i}"}) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    engines = {id(handler.manager.get(f"c{i}").instance.driver.engine) for i in range(6)}
    assert len(engines) == 6
    assert overlap > 1


def test_a2a_agent_that_cannot_be_rebuilt_serves_one_conversation() -> None:
    from mas.ctl.session.exposure import TooManyConversations

    instance = _stop_instance()
    instance.conversation_factory = None
    handler = make_runtime_handler(instance)
    handler("a", session_id="one")
    handler("again", session_id="one")
    with pytest.raises(TooManyConversations):
        handler("b", session_id="two")


def test_closing_the_handler_ends_the_conversations_it_opened() -> None:
    closed: list[str] = []

    def build():
        instance = _stop_instance()
        instance.conversation_close = lambda: closed.append("x")
        instance.conversation_factory = build
        return instance

    handler = make_runtime_handler(build())
    handler("a", session_id="one")
    handler("b", session_id="two")
    handler.close_conversations()
    assert closed == ["x", "x"]


def test_a2a_refuses_new_conversations_past_the_limit() -> None:
    from mas.ctl.session.exposure import TooManyConversations

    handler = make_runtime_handler(_stop_instance(), max_conversations=2)
    handler("a", session_id="one")
    handler("b", session_id="two")
    handler("again", session_id="one")
    with pytest.raises(TooManyConversations):
        handler("c", session_id="three")


def test_a2a_http_two_context_ids_keep_separate_histories() -> None:
    """SendMessage over the A2A JSON-RPC app, two contextIds, two conversations."""
    from fastapi.testclient import TestClient
    from library_ioa.plugins.a2a.exposure import A2AExposure

    handler = make_runtime_handler(_stop_instance())
    app = A2AExposure().build_app(
        {"metadata": {"name": "solo"}, "spec": {"description": "listener"}},
        handler,
    )

    def send(client: TestClient, text: str, context_id: str) -> None:
        response = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "SendMessage",
                "params": {
                    "message": {
                        "role": "ROLE_USER",
                        "parts": [{"text": text}],
                        "messageId": f"message-{context_id}",
                        "contextId": context_id,
                    }
                },
            },
            headers={"A2A-Version": "1.0"},
        )
        assert response.status_code == 200, response.text

    with TestClient(app) as client:
        send(client, "from alice", "alice")
        send(client, "from bob", "bob")
    alice = handler.manager.get("alice")
    bob = handler.manager.get("bob")
    assert alice.instance is not bob.instance
    assert [user for user, _ in alice.instance.driver.ctx.turn_history] == ["from alice"]
    assert [user for user, _ in bob.instance.driver.ctx.turn_history] == ["from bob"]
