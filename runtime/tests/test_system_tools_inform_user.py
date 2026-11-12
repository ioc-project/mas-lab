#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for the inform_user system tool and its interaction with request_human_input."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from mas.library.standard.plugins.system_tools.inform_user import InformUserTool
from mas.library.standard.plugins.system_tools.request_human_input import RequestHumanInputTool
from mas.runtime.boundary.hitl.registry import get_hitl_resolver_registry
from mas.runtime.engine.manifest_tool_provider import build_manifest_tool_provider
from mas.runtime.system_tools.signal import InformUserSignal, RequestHitlSignal


@dataclass
class _FakeCtx:
    session_id: str = "sess-1"
    agent_id: str = "finance-agent"
    correlation_id: int = 42


USER_IO = ("request_human_input", "inform_user")


def _plugin_state(provider, name: str):
    local = provider._local_provider()
    for row in local.system_plugins:
        if row.name == name or row.urn.endswith(f".{name}"):
            return row.state
    raise AssertionError(f"system plugin {name!r} was not loaded")


@pytest.fixture()
def empty_tool_tree(tmp_path: Path) -> Path:
    return tmp_path


def test_system_tools_excluded_by_default(empty_tool_tree: Path):
    """request_human_input/inform_user are opt-in: never implicit."""
    provider = build_manifest_tool_provider([], empty_tool_tree)
    names = {t["function"]["name"] for t in provider.list_openai_tools()}
    assert names == set()


def test_system_tools_included_when_host_requests_them(empty_tool_tree: Path):
    provider = build_manifest_tool_provider([], empty_tool_tree, system_plugins=USER_IO)
    names = {t["function"]["name"] for t in provider.list_openai_tools()}
    assert names == {"request_human_input", "inform_user"}


def test_manifest_declaration_enables_only_the_listed_system_tool(empty_tool_tree: Path):
    provider = build_manifest_tool_provider([], empty_tool_tree, spec={"system_plugins": ["inform_user"]})
    names = {t["function"]["name"] for t in provider.list_openai_tools()}
    assert names == {"inform_user"}


def test_unknown_system_tool_is_rejected(empty_tool_tree: Path):
    from mas.runtime.registry.provider_protocol import ManifestToolLoadError

    with pytest.raises(ManifestToolLoadError, match="unknown system plugin"):
        build_manifest_tool_provider([], empty_tool_tree, spec={"system_plugins": ["infrom_user"]})


def test_unknown_system_tool_is_rejected_even_when_disabled(empty_tool_tree: Path):
    from mas.runtime.registry.provider_protocol import ManifestToolLoadError

    with pytest.raises(ManifestToolLoadError, match="unknown system plugin"):
        build_manifest_tool_provider(
            [],
            empty_tool_tree,
            spec={"system_plugins": [{"rqeuest_human_input": {"enabled": False}}]},
        )


def test_inform_user_tool_raises_signal_with_expected_fields():
    tool = InformUserTool()
    with pytest.raises(InformUserSignal) as exc_info:
        tool.execute(
            message="Still negotiating with Finance.",
            user_name="jordan",
            involved_agents=["finance-agent"],
            metadata={"round": 2},
        )
    signal = exc_info.value
    assert signal.message == "Still negotiating with Finance."
    assert signal.user_name == "jordan"
    assert signal.involved_agents == ["finance-agent"]
    assert signal.metadata == {"round": 2}


def test_inform_user_wrapper_registers_update_in_registry_fallback(empty_tool_tree: Path, monkeypatch):
    provider = build_manifest_tool_provider([], empty_tool_tree, system_plugins=USER_IO)
    ctx = _FakeCtx()

    result = provider.call_tool(
        "inform_user",
        {"message": "Still reviewing renewal economics."},
        ctx=ctx,
    )

    assert result["status"] == "sent"
    assert result["blocking"] is False
    assert result["message"] == "Still reviewing renewal economics."

    registry = get_hitl_resolver_registry()
    pending = registry.get_pending_user_updates_for_session(ctx.session_id)
    assert ctx.agent_id in pending
    assert pending[ctx.agent_id][0].message == "Still reviewing renewal economics."

    registry.clear_session(ctx.session_id)
    assert registry.get_pending_user_updates_for_session(ctx.session_id) == {}


def test_inform_user_wrapper_routes_through_user_io_contract(empty_tool_tree: Path):
    calls = []

    class _FakeUserIOContract:
        def send_progress_update(self, **kwargs):
            calls.append(kwargs)
            return {"delivered": True}

    provider = build_manifest_tool_provider(
        [],
        empty_tool_tree,
        system_plugins=USER_IO,
        user_io_contract=_FakeUserIOContract(),
    )
    ctx = _FakeCtx()

    result = provider.call_tool(
        "inform_user",
        {"message": "Progress update via contract."},
        ctx=ctx,
    )

    assert result["status"] == "sent"
    assert result["receipt"] == {"delivered": True}
    assert calls[0]["message"] == "Progress update via contract."
    assert calls[0]["agent_id"] == ctx.agent_id

    # Contract path must not also register a registry fallback entry.
    registry = get_hitl_resolver_registry()
    assert registry.get_pending_user_updates_for_session(ctx.session_id) == {}


def test_request_human_input_still_auto_resolves_in_batch_mode(empty_tool_tree: Path):
    """Regression check: refactoring the wrapper into a shared base class must not
    change the auto-resolve behavior for request_human_input."""
    from mas.library.standard.plugins.hitl.contracts import AutoResolveHitlContract

    provider = build_manifest_tool_provider(
        [], empty_tool_tree, system_plugins=USER_IO, hitl_contract=AutoResolveHitlContract()
    )
    ctx = _FakeCtx()

    result = provider.call_tool(
        "request_human_input",
        {
            "question": "Approve 20% discount?",
            "question_type": "CONFIRM",
            "choices": ["approve", "reject"],
        },
        ctx=ctx,
    )

    assert result["choice"] == "approve"
    assert result["steering"] == ""


def test_request_human_input_tool_still_raises_hitl_signal():
    tool = RequestHumanInputTool()
    with pytest.raises(RequestHitlSignal) as exc_info:
        tool.execute(question="Approve?", question_type="CONFIRM", choices=["approve", "reject"])
    assert exc_info.value.question == "Approve?"


def test_default_max_message_length_matches_schema_constant():
    from mas.library.standard.plugins.system_tools.inform_user import DEFAULT_MAX_MESSAGE_LENGTH

    tool = InformUserTool()
    assert tool.max_message_length == DEFAULT_MAX_MESSAGE_LENGTH
    with pytest.raises(ValueError):
        tool.execute(message="x" * (DEFAULT_MAX_MESSAGE_LENGTH + 1))


def test_manifest_params_configure_max_message_length(empty_tool_tree: Path):
    """issue #65 follow-up: a message that would fail the default 2000-char
    cap (e.g. a more verbose model's longer synthesis) must pass once
    max_message_length is raised via the manifest's spec.tools entry."""
    provider = build_manifest_tool_provider(
        [],
        empty_tool_tree,
        spec={"system_plugins": [{"inform_user": {"max_message_length": 8000}}]},
    )
    wrapper = _plugin_state(provider, "inform_user")
    assert wrapper._tool.max_message_length == 8000
    with pytest.raises(InformUserSignal):
        wrapper._tool.execute(message="x" * 3000)


def test_configured_max_message_length_still_rejects_beyond_it(empty_tool_tree: Path):
    provider = build_manifest_tool_provider(
        [],
        empty_tool_tree,
        spec={"system_plugins": [{"inform_user": {"max_message_length": 100}}]},
    )
    wrapper = _plugin_state(provider, "inform_user")
    with pytest.raises(ValueError):
        wrapper._tool.execute(message="x" * 101)


def test_advertised_schema_reflects_configured_max_message_length():
    tool = InformUserTool(max_message_length=8000)
    schema = tool.get_parameters_schema()
    assert schema["properties"]["message"]["maxLength"] == 8000
