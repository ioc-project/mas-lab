#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A tool a plugin offers per turn (from the live ctx) is claimed and callable on that turn."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from mas.runtime.system_plugins import CallContext, SystemPluginBase, ToolDefinition, UnknownSystemToolError
from mas.runtime.system_plugins.dispatch import SystemPluginDispatcher
from mas.runtime.system_plugins.load import BoundSystemPlugins, _Bound


class _PerTurn(SystemPluginBase):
    """Offers ``late_tool`` only when the turn ctx carries ``enabled``; nothing at load time."""

    def tools(self, ctx: Any | None = None):
        return (ToolDefinition(name="late_tool"),) if getattr(ctx, "enabled", False) else ()

    def bind(self, session):
        return object()

    def call(self, tool, args, *, state, context):
        return {"tool": tool, "ctx_enabled": getattr(context.ctx, "enabled", False)}


def _bound() -> BoundSystemPlugins:
    bound = BoundSystemPlugins()
    plugin = _PerTurn()
    bound.add(_Bound(name="per_turn", urn="per_turn", plugin=plugin, state=plugin.bind(None), settings={}))
    return bound


def test_ctx_dependent_tool_is_claimed_only_with_the_live_ctx() -> None:
    bound = _bound()
    live = SimpleNamespace(enabled=True)
    assert bound.claims("late_tool", ctx=live)
    assert not bound.claims("late_tool")
    assert not bound.claims("late_tool", ctx=SimpleNamespace(enabled=False))


async def test_ctx_dependent_tool_is_called_through_the_live_ctx() -> None:
    bound = _bound()
    live = SimpleNamespace(enabled=True)
    assert (await bound.acall("late_tool", {}, context=CallContext(ctx=live)))["ctx_enabled"] is True
    with pytest.raises(UnknownSystemToolError):
        await bound.acall("late_tool", {})


def test_dispatcher_forwards_ctx_to_claims() -> None:
    dispatcher = SystemPluginDispatcher(_bound())
    assert dispatcher.claims("late_tool", ctx=SimpleNamespace(enabled=True))
    assert not dispatcher.claims("late_tool")


async def test_engine_dispatch_claims_a_ctx_dependent_tool_with_the_live_ctx() -> None:
    """The engine's tool path hands the turn ctx to the contract, so the tool is claimed and run."""
    from mas.runtime.engine.tool_dispatch import aexecute_engine_tool

    live = SimpleNamespace(enabled=True)
    result = await aexecute_engine_tool(
        "late_tool", engine_contracts=[SystemPluginDispatcher(_bound())], ctx=live, arguments={}
    )
    assert isinstance(result, str)  # the model reads text: a dict result is formatted, not passed on
    assert json.loads(result) == {"tool": "late_tool", "ctx_enabled": True}


class _Quiet(_PerTurn):
    def call(self, tool, args, *, state, context):
        return None if args.get("none") else {"opaque": object()}


async def test_engine_dispatch_gives_the_model_empty_text_for_none_and_str_for_opaque_values() -> None:
    from mas.runtime.engine.tool_dispatch import aexecute_engine_tool

    bound = BoundSystemPlugins()
    plugin = _Quiet()
    bound.add(_Bound(name="quiet", urn="quiet", plugin=plugin, state=plugin.bind(None), settings={}))
    live = SimpleNamespace(enabled=True)
    contracts = [SystemPluginDispatcher(bound)]

    assert await aexecute_engine_tool("late_tool", engine_contracts=contracts, ctx=live, arguments={"none": 1}) == ""
    opaque = await aexecute_engine_tool("late_tool", engine_contracts=contracts, ctx=live, arguments={})
    assert "object object at" in json.loads(opaque)["opaque"]
