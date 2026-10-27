#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared agent_expose listeners for serve / chat / tui / run-mas."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from mas.ctl.cli.bind_flags import with_connects
from mas.ctl.compose.models import ResolvedInfra
from mas.ctl.compose.runner import ComposeResult
from mas.ctl.executor.run_mas import execute_run_mas
from mas.ctl.session.engine_factory import EngineSelection
from mas.ctl.session.exposure import (
    close_exposures,
    expose_agent,
    exposure_targets,
    make_runtime_handler,
    start_hosted_exposures,
    start_materialized_exposures,
)
from mas.runtime.engine.simulated import SimulatedEngine


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
    assert exposure_targets(
        ["moderator", "schedule_agent"],
        {
            "schedule_agent": {
                "protocol": "a2a",
                "usage": "use",
                "url": "http://127.0.0.1:9006",
            }
        },
        conversation_ids=["moderator"],
    ) == []


def test_exposure_targets_skips_use_only_and_missing_protocol() -> None:
    assert exposure_targets(
        ["assistant", "oracle", "weather"],
        {
            "assistant": {"protocol": "a2a", "usage": "use", "url": "http://127.0.0.1:9001"},
            "oracle": {"url": "http://127.0.0.1:9005"},
            "weather": {"protocol": "mcp", "usage": "deploy", "url": "http://127.0.0.1:9001/mcp"},
        },
        conversation_ids=["assistant"],
    ) == []


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
    assert started == handles
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

    assert started == [handle]
    exposure.serve_background.assert_called_once()
    close_exposures(started)


def test_expose_agent_blocking_is_the_serve_path() -> None:
    instance = SimpleNamespace(driver=SimpleNamespace())
    exposure = MagicMock()
    endpoint = {"protocol": "a2a", "usage": "deploy", "url": "http://127.0.0.1:9005"}

    with patch("mas.runtime.registry.get_registry", return_value=SimpleNamespace(create=lambda *a, **k: exposure)):
        assert expose_agent(
            instance,
            {"metadata": {"name": "qa-agent"}},
            endpoint,
            blocking=True,
        ) is None

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


def test_runtime_handler_forwards_turn_and_session_ids() -> None:
    calls: list[tuple[str, str, str, str, int | None]] = []

    class Runtime:
        def run_user_text(
            self,
            text: str,
            *,
            turn_id: str,
            session_id: str,
            parent_call_id: str,
            upstream_correlation_id: int | None,
        ) -> SimpleNamespace:
            calls.append((text, turn_id, session_id, parent_call_id, upstream_correlation_id))
            return SimpleNamespace(client_responses=[SimpleNamespace(content="answer")])

    handler = make_runtime_handler(Runtime())
    assert handler(
        "question",
        turn_id="turn-1",
        session_id="session-1",
        parent_call_id="parent-1",
        upstream_correlation_id=42,
    ) == {"text": "answer", "context_id": "session-1"}
    assert calls == [("question", "turn-1", "session-1", "parent-1", 42)]


def _free_port() -> int:
    import socket

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class _ReplyRuntime:
    def run_user_text(self, *_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            client_responses=[SimpleNamespace(content="ok")],
            awaiting_hitl=False,
        )


def test_two_hosted_use_and_deploy_endpoints_serve_a2a_agent_cards() -> None:
    import httpx

    port_assistant = _free_port()
    port_greeter = _free_port()
    handles = start_hosted_exposures(
        {"assistant": _ReplyRuntime(), "greeter": _ReplyRuntime()},
        {
            "assistant": {
                "protocol": "a2a",
                "usage": "use-and-deploy",
                "url": f"http://127.0.0.1:{port_assistant}",
            },
            "greeter": {
                "protocol": "a2a",
                "usage": "use-and-deploy",
                "url": f"http://127.0.0.1:{port_greeter}",
            },
        },
        conversation_ids=["assistant"],
        manifests={
            "assistant": {"metadata": {"name": "assistant"}, "spec": {"description": "entry"}},
            "greeter": {"metadata": {"name": "greeter"}, "spec": {"description": "peer"}},
        },
    )
    try:
        assert len(handles) == 2
        assistant = httpx.get(
            f"http://127.0.0.1:{port_assistant}/.well-known/agent-card.json",
            timeout=5,
        )
        greeter = httpx.get(
            f"http://127.0.0.1:{port_greeter}/.well-known/agent-card.json",
            timeout=5,
        )
        assert assistant.status_code == 200
        assert greeter.status_code == 200
        assert assistant.json()["name"] == "assistant"
        assert greeter.json()["name"] == "greeter"
    finally:
        close_exposures(handles)


def _two_agent_mas(tmp_path):
    from pathlib import Path

    import yaml

    agent_yaml = """apiVersion: mas/v1
kind: Agent
metadata:
  name: {name}
spec:
  description: test
  models:
    - model: gpt-4o-mini
"""
    (tmp_path / "assistant.yaml").write_text(agent_yaml.format(name="assistant"), encoding="utf-8")
    (tmp_path / "greeter.yaml").write_text(agent_yaml.format(name="greeter"), encoding="utf-8")
    mas_path = tmp_path / "mas.yaml"
    mas_path.write_text(
        """apiVersion: mas/v1
kind: MAS
metadata:
  name: greet-fixture
spec:
  agency:
    agents:
      - id: assistant
        ref: assistant.yaml
      - id: greeter
        ref: greeter.yaml
  workflow:
    entry: assistant
    nodes:
      - id: assistant
        delegates_to: [greeter]
      - id: greeter
""",
        encoding="utf-8",
    )
    port_assistant = _free_port()
    port_greeter = _free_port()
    infra_path = tmp_path / "a2a-agents.yaml"
    infra_path.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "infra/v1",
                "kind": "Application",
                "metadata": {"name": "a2a-agents"},
                "spec": {
                    "endpoints": {
                        "assistant": {
                            "protocol": "a2a",
                            "usage": "use-and-deploy",
                            "url": f"http://127.0.0.1:{port_assistant}",
                        },
                        "greeter": {
                            "protocol": "a2a",
                            "usage": "use-and-deploy",
                            "url": f"http://127.0.0.1:{port_greeter}",
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return Path(mas_path), infra_path, port_assistant, port_greeter


def test_run_mas_infra_ref_two_endpoints_listen_as_a2a(tmp_path) -> None:
    import httpx

    mas_path, infra_path, port_assistant, port_greeter = _two_agent_mas(tmp_path)
    cards: dict[str, str] = {}

    mock_resp = SimpleNamespace(content="hello", finish_reason="stop")
    mock_trace = SimpleNamespace(client_responses=[mock_resp], boundary_errors=[])
    mock_turn = SimpleNamespace(
        text="hello",
        awaiting_hitl=False,
        trace=mock_trace,
        responses=[mock_resp],
    )
    sel = EngineSelection(
        engine=SimulatedEngine(llm_next_step=lambda _cid: "STOP", stop_text="hello"),
        mode="injected",
    )

    def _run_turn(*_args: object, **_kwargs: object):
        for name, port in (("assistant", port_assistant), ("greeter", port_greeter)):
            response = httpx.get(f"http://127.0.0.1:{port}/.well-known/agent-card.json", timeout=5)
            assert response.status_code == 200, response.text
            cards[name] = response.json()["name"]
        return mock_turn

    with patch("mas.ctl.session.bootstrap.build_engine", return_value=sel):
        with patch("mas.ctl.session.controller.SessionController.run_turn", side_effect=_run_turn):
            rc = execute_run_mas(
                mas_path,
                prompt="hello",
                validate=False,
                infra_refs=[str(infra_path)],
                auto_hitl=True,
            )

    assert rc == 0
    assert cards == {"assistant": "assistant", "greeter": "greeter"}


def test_run_mas_bind_greeter_does_not_fail_validation(tmp_path) -> None:
    import httpx

    mas_path, infra_path, port_assistant, port_greeter = _two_agent_mas(tmp_path)
    listening: dict[str, int] = {}

    mock_resp = SimpleNamespace(content="hello", finish_reason="stop")
    mock_trace = SimpleNamespace(client_responses=[mock_resp], boundary_errors=[])
    mock_turn = SimpleNamespace(
        text="hello",
        awaiting_hitl=False,
        trace=mock_trace,
        responses=[mock_resp],
    )
    sel = EngineSelection(
        engine=SimulatedEngine(llm_next_step=lambda _cid: "STOP", stop_text="hello"),
        mode="injected",
    )

    def _run_turn(*_args: object, **_kwargs: object):
        listening["assistant"] = httpx.get(
            f"http://127.0.0.1:{port_assistant}/.well-known/agent-card.json",
            timeout=5,
        ).status_code
        try:
            listening["greeter"] = httpx.get(
                f"http://127.0.0.1:{port_greeter}/.well-known/agent-card.json",
                timeout=0.5,
            ).status_code
        except httpx.HTTPError:
            listening["greeter"] = 0
        return mock_turn

    with patch("mas.ctl.session.bootstrap.build_engine", return_value=sel):
        with patch("mas.ctl.session.controller.SessionController.run_turn", side_effect=_run_turn):
            rc = execute_run_mas(
                mas_path,
                prompt="hello",
                validate=False,
                infra_refs=[str(infra_path)],
                overrides=list(
                    with_connects(
                        connects=(f"greeter=a2a://127.0.0.1:{port_greeter}/agents/greeter",),
                    )
                ),
                auto_hitl=True,
            )

    assert rc == 0
    assert listening["assistant"] == 200
    assert listening["greeter"] == 0

