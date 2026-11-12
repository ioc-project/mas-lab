#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest
from mas.library.skills.plugins.system_tools import ActivateSkillPlugin, RunSkillScriptSystemPlugin
from mas.library.standard.plugins.system_tools.control import PauseSessionPlugin
from mas.library.standard.plugins.system_tools.inform_user import InformUserPlugin
from mas.library.standard.plugins.system_tools.request_human_input import RequestHumanInputPlugin
from mas.library.standard.plugins.system_tools.send import SendPlugin
from mas.library.standard.plugins.system_tools.spawn_subagent import SubagentsPlugin
from mas.runtime.system_plugins import SessionContext, SystemPluginBase, ToolDefinition, UnknownSystemToolError

from .conformance import (
    assert_is_system_plugin,
    assert_session_state_isolated,
    assert_snapshot_restore,
    assert_unique_tool_names,
    assert_unknown_tool_raises,
)

_PLUGINS = (
    InformUserPlugin,
    RequestHumanInputPlugin,
    PauseSessionPlugin,
    SendPlugin,
    SubagentsPlugin,
    ActivateSkillPlugin,
    RunSkillScriptSystemPlugin,
)


@pytest.mark.parametrize("plugin_cls", _PLUGINS)
def test_shipped_plugin_conformance(plugin_cls: type) -> None:
    assert_is_system_plugin(plugin_cls)
    assert_unknown_tool_raises(plugin_cls)
    assert_snapshot_restore(plugin_cls)
    assert_session_state_isolated(plugin_cls, SessionContext(session_id="a"), SessionContext(session_id="b"))


def test_shipped_plugins_do_not_share_tool_names() -> None:
    templates = [{"id": "w", "description": "w"}]
    plugins = [cls(templates=templates) if cls is SubagentsPlugin else cls() for cls in _PLUGINS]
    assert_unique_tool_names(plugins)


class _SharedState(SystemPluginBase):
    name = "bad_shared"
    cache: dict = {}

    def tools(self, ctx=None):
        return ()

    def bind(self, session):
        return {}

    def call(self, tool, args, *, state, context):
        raise UnknownSystemToolError(tool)


class _SwallowsUnknown(_SharedState):
    name = "bad_swallow"
    cache = None  # type: ignore[assignment]

    def call(self, tool, args, *, state, context):
        return None


def test_conformance_rejects_mutable_class_state() -> None:
    with pytest.raises(AssertionError, match="mutable class state"):
        assert_session_state_isolated(_SharedState, SessionContext(), SessionContext())


def test_conformance_rejects_a_plugin_that_swallows_an_unknown_tool() -> None:
    with pytest.raises(AssertionError, match="did not raise"):
        assert_unknown_tool_raises(_SwallowsUnknown)


class _DropsSnapshot(SystemPluginBase):
    name = "bad_restore"

    def tools(self, ctx=None):
        return ()

    def bind(self, session):
        return {"n": 1}

    def call(self, tool, args, *, state, context):
        raise UnknownSystemToolError(tool)

    def snapshot(self, state):
        return dict(state)

    def restore(self, snapshot):
        return {"n": 0}


def test_conformance_rejects_restore_that_drops_snapshot() -> None:
    with pytest.raises(AssertionError):
        assert_snapshot_restore(_DropsSnapshot)


def test_subagents_plugin_snapshot_restores_the_spawn_ledger() -> None:
    plugin = SubagentsPlugin(max_spawns=1, max_depth=1)
    state = plugin.bind(SessionContext())
    assert state.ledger.allow_spawn("s", "root")
    state.ledger.enter("s", child_agent_id="root.w.1", parent_agent_id="root")
    assert state.ledger.spawn_count("s") == 1
    snap = plugin.snapshot(state)
    restored = plugin.restore(snap)
    assert restored.ledger.spawn_count("s") == 1
    assert restored.ledger.agent_depth("s", "root.w.1") == 1
    assert not restored.ledger.allow_spawn("s", "root")
    assert not restored.ledger.allow_spawn("s", "root.w.1")


def test_instance_plugin_restore_refuses_a_snapshot_it_cannot_apply() -> None:
    plugin = InformUserPlugin()
    plugin.bind(SessionContext())
    with pytest.raises(NotImplementedError, match="cannot restore"):
        plugin.restore({"kept": 1})


def test_conformance_rejects_colliding_tool_names() -> None:
    class _A(_SwallowsUnknown):
        name = "a"

        def tools(self, ctx=None):
            return (ToolDefinition(name="same"),)

    class _B(_A):
        name = "b"

    with pytest.raises(AssertionError, match="exposed by"):
        assert_unique_tool_names([_A(), _B()])


def test_subagents_restore_keeps_spec_ceilings_and_saved_spend() -> None:
    from mas.library.standard.plugins.system_tools.spawn_subagent import SubagentsPlugin

    plugin = SubagentsPlugin(max_spawns=2, max_depth=1)
    state = plugin.bind(SessionContext())
    state.ledger.enter("s", "a.t.1", "a")
    saved = dict(plugin.snapshot(state))

    # an older checkpoint without plugin state must not widen the budget to the defaults
    empty = plugin.restore({})
    assert (empty.ledger.max_spawns, empty.ledger.max_depth) == (2, 1)

    # a spec that raised the ceilings since the checkpoint wins over the saved ceilings, spend is kept
    raised = SubagentsPlugin(max_spawns=5, max_depth=2)
    raised.bind(SessionContext())
    restored = raised.restore(saved)
    assert (restored.ledger.max_spawns, restored.ledger.max_depth) == (5, 2)
    assert restored.ledger.spawn_count("s") == 1


def test_send_plugin_owns_the_delegation_and_claims_only_offered_peers() -> None:
    from mas.library.standard.plugins.agentcomm.local import LocalAgentComm
    from mas.runtime.system_plugins import CallContext
    from mas.runtime.system_plugins.load import load_system_plugins

    bound = load_system_plugins({"system_plugins": ["send"]})
    tool = bound.state_for_capability("delegation")
    assert not bound.claims("delegate_to_worker")  # nothing offered before the engine wires peers

    seen: list[tuple[str, str]] = []
    tool.configure(
        comm=LocalAgentComm(lambda agent, text, *_: seen.append((agent, text)) or "done"),
        routes={},
        allowed_peers=["worker"],
    )
    assert bound.claims("delegate_to_worker")
    assert not bound.claims("delegate_to_other")  # a name the model was not offered is not claimed
    assert bound.call("delegate_to_worker", {"task": "go"}, context=CallContext()) == "done"
    assert seen == [("worker", "go")]
    with pytest.raises(UnknownSystemToolError):
        bound.call("delegate_to_other", {"task": "go"}, context=CallContext())
