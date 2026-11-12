#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from mas.runtime.deprecation import reset_deprecation_warnings
from mas.runtime.registry import register_plugin, unregister_plugin
from mas.runtime.registry.provider_protocol import ManifestToolLoadError
from mas.runtime.system_plugins import (
    CallContext,
    SessionContext,
    SystemPluginBase,
    ToolDefinition,
    UnknownSystemToolError,
    load_system_plugins,
)


class _Probe(SystemPluginBase):
    """One tool ``probe_<name>``; the state is a counter, so a snapshot carries something."""

    tool = "probe"

    def tools(self, ctx: Any | None = None):
        return (ToolDefinition(name=self.tool, description="probe"),)

    def bind(self, session: SessionContext) -> object:
        self.count = {"calls": 0}
        return self.count

    def call(self, tool, args, *, state, context):
        if tool != self.tool:
            raise UnknownSystemToolError(tool)
        state["calls"] += 1
        return state["calls"]

    def snapshot(self, state):
        return dict(state)

    def restore(self, snapshot):
        self.count = dict(snapshot)
        return self.count


class _DefaultOn(_Probe):
    name = "probe_default_on"
    tool = "probe_default_on"


class _Implied(_Probe):
    name = "probe_implied"
    tool = "probe_implied"

    @classmethod
    def should_load(cls, spec, settings, *, session):
        return bool(spec.get("probe_flag"))


class _Clone(_Probe):
    name = "probe_clone"
    tool = "inform_user"


@contextmanager
def _registered(cls: type, **attributes: Any) -> Iterator[str]:
    urn = f"mas.system_plugin.{cls.name}"
    register_plugin(
        urn,
        cls,
        shortcuts=[cls.name],
        attributes={"plugin_type": "system_plugin", "default_enabled": False, **attributes},
    )
    try:
        yield urn
    finally:
        unregister_plugin(urn)


@pytest.fixture(autouse=True)
def _fresh_warnings() -> None:
    reset_deprecation_warnings()


def test_unnamed_does_not_load_opt_in_plugins() -> None:
    bound = load_system_plugins({})
    for tool in ("inform_user", "request_human_input", "create_subagent", "pause_session"):
        assert tool not in bound.tool_names()


def test_named_system_plugins_load() -> None:
    bound = load_system_plugins({"system_plugins": ["inform_user"]})
    assert "inform_user" in bound.tool_names()
    assert "request_human_input" not in bound.tool_names()


def test_enabled_false_turns_a_named_plugin_off() -> None:
    bound = load_system_plugins({"system_plugins": [{"inform_user": {"enabled": False}}]})
    assert "inform_user" not in bound.tool_names()


def test_unknown_name_is_a_load_error() -> None:
    with pytest.raises(ManifestToolLoadError, match="unknown system plugin"):
        load_system_plugins({"system_plugins": ["infrom_user"]})


def test_enabled_false_of_unknown_plugin_is_a_load_error() -> None:
    with pytest.raises(ManifestToolLoadError, match="unknown system plugin"):
        load_system_plugins({"system_plugins": [{"no_such_plugin": {"enabled": False}}]})


def test_urn_names_the_plugin_and_no_fragment_does() -> None:
    assert "inform_user" in load_system_plugins({"system_plugins": ["mas.system_plugin.inform_user"]}).tool_names()
    for fragment in ("system_plugin.inform_user", "user", "inform"):
        with pytest.raises(ManifestToolLoadError, match="unknown system plugin"):
            load_system_plugins({"system_plugins": [fragment]})


def test_two_names_of_one_plugin_are_one_plugin() -> None:
    bound = load_system_plugins({"system_plugins": ["inform_user", "mas.system_plugin.inform_user"]})
    assert [row.urn for row in bound] == ["mas.system_plugin.inform_user"]


def test_create_subagent_shortcut_loads_subagents() -> None:
    bound = load_system_plugins(
        {"system_plugins": [{"create_subagent": {"templates": [{"id": "w", "description": "w"}]}}]}
    )
    assert "create_subagent" in bound.tool_names()


def test_deprecated_kind_system_loads_the_same_plugin_and_warns_once(caplog: pytest.LogCaptureFixture) -> None:
    spec = {"tools": [{"kind": "system", "name": "inform_user", "params": {"max_message_length": 7}}]}
    with caplog.at_level(logging.WARNING):
        first = load_system_plugins(spec)
        load_system_plugins(spec)
    assert "inform_user" in first.tool_names()
    assert [row.settings for row in first] == [{"max_message_length": 7}]
    warnings = [r.getMessage() for r in caplog.records if "kind: system" in r.getMessage()]
    assert len(warnings) == 1
    assert "spec.system_plugins" in warnings[0]


def test_deprecated_kind_system_enabled_false_turns_the_plugin_off() -> None:
    spec = {
        "system_plugins": ["inform_user"],
        "tools": [{"kind": "system", "name": "request_human_input", "enabled": False}],
    }
    bound = load_system_plugins(spec)
    assert "request_human_input" not in bound.tool_names()
    assert "inform_user" in bound.tool_names()


def test_both_spellings_the_new_key_wins(caplog: pytest.LogCaptureFixture) -> None:
    spec = {
        "system_plugins": [{"inform_user": {"max_message_length": 3}}],
        "tools": [{"kind": "system", "name": "inform_user", "params": {"max_message_length": 99}}],
    }
    with caplog.at_level(logging.WARNING):
        bound = load_system_plugins(spec)
    assert [row.settings for row in bound] == [{"max_message_length": 3}]
    assert len([r for r in caplog.records if "kind: system" in r.getMessage()]) == 1
    off = {"system_plugins": [{"inform_user": {"enabled": False}}], "tools": spec["tools"]}
    assert "inform_user" not in load_system_plugins(off).tool_names()


def test_deprecated_kind_system_unknown_name_is_a_load_error() -> None:
    with pytest.raises(ManifestToolLoadError, match="unknown system plugin"):
        load_system_plugins({"tools": [{"kind": "system", "name": "nope"}]})


def test_spawn_subagent_is_a_deprecated_name_of_subagents(caplog: pytest.LogCaptureFixture) -> None:
    spec = {"system_plugins": [{"spawn_subagent": {"templates": [{"id": "w", "description": "w"}]}}]}
    with caplog.at_level(logging.WARNING):
        bound = load_system_plugins(spec)
        load_system_plugins(spec)
    assert "create_subagent" in bound.tool_names()
    warnings = [r.getMessage() for r in caplog.records if "spawn_subagent" in r.getMessage()]
    assert len(warnings) == 1
    assert "'subagents'" in warnings[0]


def test_enabled_false_on_a_shortcut_turns_off_the_plugin() -> None:
    bound = load_system_plugins(
        {
            "system_plugins": [
                {"subagents": {"templates": [{"id": "w", "description": "w"}]}},
                {"create_subagent": {"enabled": False}},
            ]
        }
    )
    assert "create_subagent" not in bound.tool_names()


def test_default_enabled_plugin_loads_unnamed_and_turns_off_with_enabled_false() -> None:
    with _registered(_DefaultOn, default_enabled=True):
        assert "probe_default_on" in load_system_plugins({}).tool_names()
        off = load_system_plugins({"system_plugins": [{"probe_default_on": {"enabled": False}}]})
        assert "probe_default_on" not in off.tool_names()
        named = load_system_plugins({"system_plugins": ["probe_default_on"]})
        assert [row.urn for row in named].count("mas.system_plugin.probe_default_on") == 1


def test_a_plugin_that_is_not_default_enabled_needs_a_grant() -> None:
    with _registered(_DefaultOn):
        assert "probe_default_on" not in load_system_plugins({}).tool_names()
        assert "probe_default_on" in load_system_plugins({"system_plugins": ["probe_default_on"]}).tool_names()


def test_should_load_grants_a_plugin_and_enabled_false_revokes_it() -> None:
    with _registered(_Implied):
        assert "probe_implied" not in load_system_plugins({}).tool_names()
        assert "probe_implied" in load_system_plugins({"probe_flag": True}).tool_names()
        off = load_system_plugins({"probe_flag": True, "system_plugins": [{"probe_implied": {"enabled": False}}]})
        assert "probe_implied" not in off.tool_names()


def test_duplicate_tool_names_across_plugins_are_a_load_error() -> None:
    with _registered(_Clone):
        with pytest.raises(ManifestToolLoadError, match="both expose tool"):
            load_system_plugins({"system_plugins": ["inform_user", "probe_clone"]})


class _Plain:
    name = "probe_plain"


def test_a_class_that_is_not_a_system_plugin_is_refused() -> None:
    urn = "mas.system_plugin.probe_plain"
    register_plugin(urn, _Plain, shortcuts=["probe_plain"], attributes={"plugin_type": "system_plugin"})
    try:
        with pytest.raises(ManifestToolLoadError, match="derive from SystemPluginBase"):
            load_system_plugins({"system_plugins": ["probe_plain"]})
    finally:
        unregister_plugin(urn)


def test_host_requested_loads_and_enabled_false_in_the_spec_wins() -> None:
    assert "inform_user" in load_system_plugins({}, host_requested=("inform_user",)).tool_names()
    spec = {"system_plugins": [{"inform_user": {"enabled": False}}]}
    assert "inform_user" not in load_system_plugins(spec, host_requested=("inform_user",)).tool_names()


def test_two_sessions_of_one_agent_do_not_share_state_and_a_snapshot_restores_it() -> None:
    with _registered(_DefaultOn):
        spec = {"system_plugins": ["probe_default_on"]}
        one, two = load_system_plugins(spec), load_system_plugins(spec)
        assert one.call("probe_default_on", {}) == 1
        assert one.call("probe_default_on", {}) == 2
        assert two.call("probe_default_on", {}) == 1
        snapshot = one.snapshot()
        assert snapshot == {"mas.system_plugin.probe_default_on": {"calls": 2}}
        fresh = load_system_plugins(spec)
        fresh.restore(snapshot)
        assert fresh.call("probe_default_on", {}, context=CallContext()) == 3
        assert two.call("probe_default_on", {}) == 2


class _LegacyTool:
    """A third-party tool registered under the deprecated ``system_tool`` type."""

    def on_collect_tools(self, *, ctx=None, **_):
        return [{"name": "legacy_probe", "description": "legacy", "parameters": {"type": "object", "properties": {}}}]

    def on_execute_tool(self, tool_name, arguments, **_):
        return "legacy-ok"


def test_settings_for_capability_raises_on_unknown_grant() -> None:
    from mas.runtime.system_plugins.load import settings_for_capability

    with pytest.raises(ManifestToolLoadError, match="unknown system plugin"):
        settings_for_capability({"system_plugins": ["nope"]}, "orchestration")


def test_kind_system_without_a_name_is_a_load_error() -> None:
    with pytest.raises(ManifestToolLoadError, match="needs a name"):
        load_system_plugins({"tools": [{"kind": "system"}]})


def test_host_system_tools_argument_still_loads_and_warns(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    from mas.library.standard.plugins.tools.local import load_local_tool_provider

    with caplog.at_level(logging.WARNING):
        local = load_local_tool_provider([], tmp_path, system_tools=["inform_user"])
        load_local_tool_provider([], tmp_path, system_tools=["inform_user"])
    assert local.system_plugins is not None
    assert "inform_user" in local.system_plugins.tool_names()
    warnings = [r.getMessage() for r in caplog.records if "system_tools=" in r.getMessage()]
    assert len(warnings) == 1
    assert "system_plugins=" in warnings[0]


def test_deprecated_system_tool_type_still_loads_and_warns_once(caplog: pytest.LogCaptureFixture) -> None:
    urn = "mas.system_tool.legacy_probe"
    register_plugin(urn, _LegacyTool, shortcuts=["legacy_probe"], attributes={"plugin_type": "system_tool"})
    try:
        spec = {"system_plugins": ["legacy_probe"]}
        with caplog.at_level(logging.WARNING):
            bound = load_system_plugins(spec)
            load_system_plugins(spec)
        assert bound.call("legacy_probe", {}) == "legacy-ok"
        assert [d["name"] for d in bound.advertise()] == ["legacy_probe"]
        warnings = [r.getMessage() for r in caplog.records if "system_tool is deprecated" in r.getMessage()]
        assert len(warnings) == 1
        assert "legacy_probe" not in load_system_plugins({}).tool_names()
    finally:
        unregister_plugin(urn)


class _LegacyWithSettings:
    def __init__(self, *, tag: str = "none") -> None:
        self.tag = tag

    def on_collect_tools(self, *, ctx=None, **_):
        return [
            {"name": "legacy_settings", "description": "legacy", "parameters": {"type": "object", "properties": {}}}
        ]

    def on_execute_tool(self, tool_name, arguments, **_):
        return self.tag


def test_deprecated_system_tool_type_applies_grant_settings() -> None:
    urn = "mas.system_tool.legacy_settings"
    register_plugin(urn, _LegacyWithSettings, shortcuts=["legacy_settings"], attributes={"plugin_type": "system_tool"})
    try:
        bound = load_system_plugins({"system_plugins": [{"legacy_settings": {"tag": "from-grant"}}]})
        assert bound.call("legacy_settings", {}) == "from-grant"
    finally:
        unregister_plugin(urn)


class _LegacyHitl:
    def on_collect_tools(self, *, ctx=None, **_):
        return [{"name": "legacy_hitl", "description": "legacy", "parameters": {"type": "object", "properties": {}}}]

    def on_execute_tool(self, tool_name, arguments, **_):
        from mas.runtime.schema.hitl import HitlQuestionType
        from mas.runtime.system_tools.signal import RequestHitlSignal

        raise RequestHitlSignal(question="Approve?", question_type=HitlQuestionType.CONFIRM, choices=["yes"])


def test_deprecated_system_tool_communication_hitl_wraps() -> None:
    from mas.library.standard.plugins.tools.local import _wrap_legacy_communication

    class _Hitl:
        def request_approval(self, **kwargs):
            return {"choice": "yes", "question": kwargs["question"]}

    urn = "mas.system_tool.legacy_hitl"
    register_plugin(
        urn,
        _LegacyHitl,
        shortcuts=["legacy_hitl"],
        attributes={"plugin_type": "system_tool", "communication": "hitl"},
    )
    try:
        bound = load_system_plugins(
            {"system_plugins": ["legacy_hitl"]},
            session=SessionContext(
                wrap_system_tool=lambda tool, communication: _wrap_legacy_communication(
                    tool, communication, hitl_contract=_Hitl(), user_io_contract=None
                )
            ),
        )
        assert bound.call("legacy_hitl", {})["choice"] == "yes"
    finally:
        unregister_plugin(urn)


class _LegacyUserIo:
    def on_collect_tools(self, *, ctx=None, **_):
        return [{"name": "legacy_user_io", "description": "legacy", "parameters": {"type": "object", "properties": {}}}]

    def on_execute_tool(self, tool_name, arguments, **_):
        from mas.runtime.system_tools.signal import InformUserSignal

        raise InformUserSignal(message="still working")


def test_deprecated_system_tool_communication_user_io_wraps() -> None:
    from mas.library.standard.plugins.tools.local import _wrap_legacy_communication

    class _UserIo:
        def send_progress_update(self, **kwargs):
            return {"ok": True}

    urn = "mas.system_tool.legacy_user_io"
    register_plugin(
        urn,
        _LegacyUserIo,
        shortcuts=["legacy_user_io"],
        attributes={"plugin_type": "system_tool", "communication": "user_io"},
    )
    try:
        bound = load_system_plugins(
            {"system_plugins": ["legacy_user_io"]},
            session=SessionContext(
                wrap_system_tool=lambda tool, communication: _wrap_legacy_communication(
                    tool, communication, hitl_contract=None, user_io_contract=_UserIo()
                )
            ),
        )
        result = bound.call("legacy_user_io", {})
        assert result["status"] == "sent"
        assert result["message"] == "still working"
    finally:
        unregister_plugin(urn)
