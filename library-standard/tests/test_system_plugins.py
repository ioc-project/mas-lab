# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""The shipped system plugins: tools, binding, calls, wrappers and the local provider."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from mas.library.standard.plugins.hitl.contracts import AutoResolveHitlContract
from mas.library.standard.plugins.system_tools.control import ControlTools, PauseSessionPlugin
from mas.library.standard.plugins.system_tools.inform_user import InformUserPlugin
from mas.library.standard.plugins.system_tools.request_human_input import RequestHumanInputPlugin
from mas.library.standard.plugins.system_tools.send import SendPlugin, SendTool
from mas.library.standard.plugins.system_tools.spawn_subagent import (
    SpawnSubagentTool,
    SubagentsPlugin,
    SubagentsState,
)
from mas.library.standard.plugins.system_tools.wrappers import (
    SystemToolHitlWrapper,
    SystemToolUserUpdateWrapper,
    SystemToolWrapperBase,
)
from mas.library.standard.plugins.tools.local import LocalToolProvider, _wrap_legacy_communication
from mas.runtime.system_plugins import (
    CallContext,
    SessionContext,
    SpawnLedger,
    UnknownSystemToolError,
    load_system_plugins,
)


class _UserIO:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    def send_progress_update(self, **kwargs: Any) -> str:
        self.updates.append(kwargs)
        return "receipt"


def test_inform_user_plugin_sends_through_the_session_contract() -> None:
    user_io = _UserIO()
    plugin = InformUserPlugin(max_message_length=50)
    state = plugin.bind(SessionContext(user_io_contract=user_io))
    assert isinstance(state, SystemToolUserUpdateWrapper)
    ctx = SimpleNamespace(session_id="s", agent_id="a", correlation_id=3)
    out = plugin.call(
        "inform_user", {"message": "working", "user_name": "u"}, state=state, context=CallContext(ctx=ctx)
    )
    assert out["status"] == "sent" and out["user_name"] == "u" and out["receipt"] == "receipt"
    assert user_io.updates[0]["session_id"] == "s"
    assert [t.name for t in plugin.tools()] == ["inform_user"]


def test_request_human_input_plugin_resolves_through_the_session_contract() -> None:
    plugin = RequestHumanInputPlugin(max_question_length=80, timeout=5, auto_resolve_decision="reject")
    state = plugin.bind(SessionContext(hitl_contract=AutoResolveHitlContract("approve")))
    assert isinstance(state, SystemToolHitlWrapper)
    ctx = SimpleNamespace(session_id="s", agent_id="a", correlation_id=1)
    out = plugin.call(
        "request_human_input",
        {"question": "ok?", "choices": ["approve", "reject"]},
        state=state,
        context=CallContext(ctx=ctx, user="u"),
    )
    assert out["choice"] == "reject"
    with pytest.raises(UnknownSystemToolError):
        plugin.call("nope", {}, state=state, context=CallContext())


class _Control:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def pause(self, session_id: str, *, reason: str) -> None:
        self.calls.append(("pause", session_id, reason))

    def list_checkpoints(self, session_id: str) -> list[Any]:
        return [SimpleNamespace(snapshot_id="a"), SimpleNamespace(snapshot_id="b")]

    def inspect(self, session_id: str) -> Any:
        return SimpleNamespace(
            session_id=session_id,
            status="paused",
            turn=2,
            live_snapshot_id="l",
            cursor_snapshot_id="c",
            spec_revision=1,
        )

    def navigate(self, session_id: str, *, to: str, reason: str) -> Any:
        return SimpleNamespace(snapshot_id=to)

    def cancel_inflight(self, session_id: str) -> bool:
        return True

    def run_script(self, session_id: str, *, text: str, auto_stop: bool) -> dict[str, Any]:
        return {"ran": text, "auto_stop": auto_stop}


def test_pause_session_plugin_runs_every_control_tool() -> None:
    control = _Control()
    plugin = PauseSessionPlugin()
    state = plugin.bind(SessionContext(session_id="s"))
    assert isinstance(state, ControlTools)
    ctx = SimpleNamespace(control=control, allow_control_tools=True, session_id="s")
    call = CallContext(ctx=ctx)

    def run(tool: str, **args: Any) -> str:
        return plugin.call(tool, args, state=state, context=call)

    assert run("pause_session", reason="r") == "paused"
    assert run("list_checkpoints") == "a,b"
    assert '"status": "paused"' in run("inspect_session")
    assert run("navigate_checkpoint", to="x", reason="r") == "x"
    assert run("cancel_inflight") == "cancelled"
    assert '"ran": "go"' in run("run_control_script", script="go", auto_stop=True)
    assert "unsupported" in state.on_execute_tool("other", {}, ctx=ctx)
    assert len(plugin.tools(ctx)) == 6
    assert plugin.tools(SimpleNamespace(allow_control_tools=False)) == ()
    with pytest.raises(UnknownSystemToolError):
        run("not_a_control_tool")


def test_pause_session_without_a_control_contract_is_unavailable() -> None:
    tools = ControlTools()
    assert "unavailable" in tools.on_execute_tool("pause_session", {"reason": "r"}, ctx=SimpleNamespace())
    assert tools.on_collect_tools(ctx=SimpleNamespace(allow_control_tools=True)) == []


def test_send_tool_wiring_and_offers() -> None:
    first = SendTool()
    first.configure(comm=None, routes={}, allowed_peers=["alice", " "])
    second = SendTool()
    second.adopt_wiring(first)
    assert second.offers("delegate_to_alice")
    assert not second.offers("delegate_to_bob")
    assert not second.offers("pause_session")


def test_send_plugin_claims_only_offered_peers() -> None:
    plugin = SendPlugin()
    state = plugin.bind(SessionContext())
    assert isinstance(state, SendTool)
    assert not plugin.claims("delegate_to_alice", state=state)
    state.configure(comm=None, routes={}, allowed_peers=["alice"])
    assert plugin.claims("delegate_to_alice", state=state)
    with pytest.raises(UnknownSystemToolError):
        plugin.call("delegate_to_bob", {}, state=state, context=CallContext())
    with pytest.raises(UnknownSystemToolError):
        plugin.call("delegate_to_alice", {}, state=object(), context=CallContext())
    assert not plugin.claims("delegate_to_alice", state=object())


class _Spawner:
    def __init__(self, ledger: SpawnLedger | None = None) -> None:
        self.context = SimpleNamespace(ledger=ledger)
        self.calls: list[tuple] = []

    def is_subagent_tool(self, name: str) -> bool:
        return name == "create_subagent"

    def call(self, tool: str, args: dict[str, Any], **kwargs: Any) -> str:
        self.calls.append(("call", tool, args, kwargs))
        return "ran"

    async def acall(self, tool: str, args: dict[str, Any], **kwargs: Any) -> str:
        self.calls.append(("acall", tool, args, kwargs))
        return "aran"


_TEMPLATES = [{"id": "w", "description": "worker"}, {"id": "x"}]


def test_subagents_plugin_advertises_and_fails_closed_without_a_spawner() -> None:
    plugin = SubagentsPlugin(templates=_TEMPLATES, max_spawns=2, max_depth=1)
    state = plugin.bind(SessionContext())
    assert isinstance(state, SubagentsState)
    assert state.ledger.max_spawns == 2
    [definition] = plugin.tools()
    assert definition.name == "create_subagent" and "w: worker" in definition.description
    assert SubagentsPlugin().tools() == ()
    out = plugin.call("create_subagent", {"template": "w", "task": "t"}, state=state, context=CallContext())
    assert "unavailable" in out
    assert "unavailable" in asyncio.run(
        plugin.acall("create_subagent", {"template": "w", "task": "t"}, state=state, context=CallContext())
    )
    with pytest.raises(UnknownSystemToolError):
        plugin.call("other", {}, state=state, context=CallContext())
    assert SpawnSubagentTool().on_execute_tool("other", {}).startswith("[spawn_subagent] unsupported")
    ctx = SimpleNamespace(allow_subagent_spawning=True, subagent_templates=_TEMPLATES)
    assert [s["name"] for s in SpawnSubagentTool().on_collect_tools(ctx=ctx)] == ["create_subagent"]
    assert SpawnSubagentTool().on_collect_tools(ctx=SimpleNamespace()) == []


def test_subagents_plugin_runs_the_host_spawner_sync_and_async() -> None:
    plugin = SubagentsPlugin(templates=_TEMPLATES)
    state = plugin.bind(SessionContext())
    spawner = _Spawner()
    state.spawner = spawner
    call = CallContext(correlation_id=4, caller_call_id="c")
    assert plugin.call("create_subagent", {"task": "t"}, state=state, context=call) == "ran"
    assert asyncio.run(plugin.acall("create_subagent", {"task": "t"}, state=state, context=call)) == "aran"
    assert [c[0] for c in spawner.calls] == ["call", "acall"]
    assert spawner.calls[0][3]["correlation_id"] == 4


def test_subagents_state_adopts_a_spawner_and_its_ledger_once() -> None:
    plugin = SubagentsPlugin(templates=_TEMPLATES)
    state = plugin.bind(SessionContext())
    ledger = SpawnLedger()
    spawner = _Spawner(ledger)
    assert state.adopt(spawner)
    assert state.spawner is spawner and state.ledger is ledger
    assert not state.adopt(_Spawner())
    fresh = plugin.bind(SessionContext())
    assert not fresh.adopt(object())
    other = _Spawner()
    other.is_subagent_tool = lambda name: False  # type: ignore[method-assign]
    assert not fresh.adopt(other)


def test_subagents_plugin_snapshot_and_restore_keep_the_ledger() -> None:
    plugin = SubagentsPlugin(templates=_TEMPLATES, max_spawns=3)
    with pytest.raises(RuntimeError):
        plugin.restore({})
    state = plugin.bind(SessionContext())
    ledger = state.ledger
    assert plugin.restore(plugin.snapshot(state)) is state
    assert state.ledger is ledger and state.ledger.max_spawns == 3
    assert plugin.snapshot(object()) == {}


def test_wrapper_base_forwards_collection_and_legacy_tools() -> None:
    class _Legacy:
        def get_name(self) -> str:
            return "legacy"

        def get_description(self) -> str:
            return "d"

        def get_parameters_schema(self) -> dict[str, Any]:
            return {}

        def execute(self, **kwargs: Any) -> str:
            return f"ran {kwargs}"

    base = SystemToolWrapperBase(_Legacy())
    assert base.on_collect_tools()[0]["name"] == "legacy"
    assert base._execute_wrapped("legacy", {"a": 1}, ctx=None, user="") == "ran {'a': 1}"
    assert SystemToolWrapperBase(object()).on_collect_tools() == []
    assert SystemToolWrapperBase._extract_context(None) == ("unknown", "unknown", 0)


def test_legacy_communication_wraps_the_tool_by_attribute() -> None:
    tool = object()
    assert _wrap_legacy_communication(tool, None, hitl_contract=None, user_io_contract=None) is tool
    hitl = _wrap_legacy_communication(tool, "hitl", hitl_contract=AutoResolveHitlContract(), user_io_contract=None)
    assert isinstance(hitl, SystemToolHitlWrapper)
    user_io = _wrap_legacy_communication(tool, "user_io", hitl_contract=None, user_io_contract=_UserIO())
    assert isinstance(user_io, SystemToolUserUpdateWrapper)


def test_local_provider_serves_system_plugins_before_application_tools() -> None:
    local = LocalToolProvider()
    assert not local.has_tools()
    local.system_plugins = load_system_plugins({"system_plugins": ["inform_user"]}, session=SessionContext())
    assert local.has_tools()
    assert [s["name"] for s in local.list_tools()] == ["inform_user"]
    out = local.call_tool("inform_user", {"message": "hi"}, ctx=SimpleNamespace(session_id="s"))
    assert out["status"] == "sent"
