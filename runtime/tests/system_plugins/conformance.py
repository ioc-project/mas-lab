#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Conformance checks every system plugin can import."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from mas.runtime.system_plugins import (
    CallContext,
    SessionContext,
    SystemPluginBase,
    UnknownSystemToolError,
)

PluginFactory = Callable[[], SystemPluginBase]


def assert_is_system_plugin(plugin_cls: type) -> None:
    assert issubclass(plugin_cls, SystemPluginBase), f"{plugin_cls.__name__} must derive from SystemPluginBase"
    assert plugin_cls.name, f"{plugin_cls.__name__} has no name"


def assert_unique_tool_names(plugins: Sequence[SystemPluginBase]) -> None:
    seen: dict[str, str] = {}
    for plugin in plugins:
        for definition in plugin.tools():
            if definition.name in seen:
                owner = seen[definition.name]
                raise AssertionError(f"tool {definition.name!r} exposed by {owner!r} and {plugin.name!r}")
            seen[definition.name] = plugin.name


def assert_unknown_tool_raises(factory: PluginFactory, session: SessionContext | None = None) -> None:
    plugin = factory()
    state = plugin.bind(session or SessionContext())
    try:
        plugin.call("__not_a_tool__", {}, state=state, context=CallContext())
    except UnknownSystemToolError:
        return
    raise AssertionError(f"{type(plugin).__name__}.call did not raise UnknownSystemToolError")


def assert_snapshot_restore(factory: PluginFactory, session: SessionContext | None = None) -> None:
    """``restore(snapshot(state))`` gives back a state whose snapshot is the same."""
    plugin = factory()
    state = plugin.bind(session or SessionContext())
    snap = dict(plugin.snapshot(state))
    restored = plugin.restore(snap)
    assert restored is not None
    assert dict(plugin.snapshot(restored)) == snap


def assert_session_state_isolated(factory: PluginFactory, first: SessionContext, second: SessionContext) -> None:
    """Two sessions bind distinct state, and the class keeps no mutable state they could share."""
    one, two = factory(), factory()
    state_one, state_two = one.bind(first), two.bind(second)
    assert state_one is not state_two
    shared = sorted(
        key
        for klass in type(one).__mro__
        if klass is not object
        for key, value in vars(klass).items()
        if isinstance(value, (list, dict, set)) and not key.startswith("__")
    )
    assert not shared, f"{type(one).__name__} keeps mutable class state shared by every session: {shared}"
