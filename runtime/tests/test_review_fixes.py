#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Regressions found in review of the system-plugin and call-layer work."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from mas.library.standard.lib.retry import RetryPolicy
from mas.runtime.boundary.control.contract import ControlDenied, SessionPaused
from mas.runtime.reliability.classes import FailureClass
from mas.runtime.reliability.classify import classify_http_exception, classify_tool_exception
from mas.runtime.system_plugins import CallContext, InstanceSystemPlugin, SessionContext
from mas.runtime.system_plugins.load import BoundSystemPlugins, _Bound


class _CtxTool:
    """Advertises its tool only when the turn context carries a registry, as the skill tools do."""

    def on_collect_tools(self, *, ctx: Any = None) -> list[dict[str, Any]]:
        if getattr(ctx, "registry", None) is None:
            return []
        return [{"name": "ctx_tool", "description": "d", "parameters": {"type": "object", "properties": {}}}]

    def on_execute_tool(self, tool_name: str, arguments: dict[str, Any], **_: Any) -> str:
        return f"ran {tool_name}"


class _CtxPlugin(InstanceSystemPlugin):
    name = "ctx"

    def make_instance(self, session: SessionContext | None) -> _CtxTool:
        return _CtxTool()


def _bound(plugin: InstanceSystemPlugin, *names: str) -> BoundSystemPlugins:
    state = plugin.bind(SimpleNamespace())  # type: ignore[arg-type]
    bundle = BoundSystemPlugins()
    row = _Bound(name="ctx", urn="mas.system_plugin.ctx", plugin=plugin, state=state, settings={})
    bundle._bound.append(row)
    return bundle


async def test_a_plugin_whose_tools_come_from_the_turn_context_is_claimed_and_callable() -> None:
    bundle = _bound(_CtxPlugin())
    ctx = SimpleNamespace(registry=object())
    assert not bundle.claims("ctx_tool")
    assert bundle.claims("ctx_tool", ctx)
    assert await bundle.acall("ctx_tool", {}, context=CallContext(ctx=ctx)) == "ran ctx_tool"


def test_a_failed_add_binds_nothing() -> None:
    bundle = _bound(_CtxPlugin())
    bundle._by_tool["clash"] = bundle._bound[0]

    class _Clash(_CtxPlugin):
        def tools(self, ctx: Any = None):
            from mas.runtime.system_plugins import ToolDefinition

            return (
                ToolDefinition.from_advertise(
                    {"name": "clash", "description": "d", "parameters": {"type": "object", "properties": {}}}
                ),
            )

    other = _Clash()
    row = _Bound(
        name="other", urn="mas.system_plugin.other", plugin=other, state=other.bind(SimpleNamespace()), settings={}
    )
    before = list(bundle._bound)
    with pytest.raises(Exception, match="both expose"):
        bundle.add(row)
    assert bundle._bound == before


def test_require_idempotent_blocks_every_failure_class_for_a_non_idempotent_call() -> None:
    policy = RetryPolicy.tools_default()
    assert not policy.allows(FailureClass.UNAVAILABLE, idempotent=False)
    assert not policy.allows(FailureClass.TRANSIENT, idempotent=False)
    assert policy.allows(FailureClass.UNAVAILABLE, idempotent=True)


def test_a_number_inside_a_message_is_not_a_rate_limit() -> None:
    assert classify_http_exception(ValueError("invalid id 4290")).failure_class == FailureClass.APPLICATION
    assert classify_http_exception(RuntimeError("HTTP 429 from upstream")).failure_class == FailureClass.TRANSIENT


@pytest.mark.parametrize(
    "exc", [SessionPaused("s", reason="timeout waiting, connection reset"), ControlDenied("m", "s", reason="503")]
)
def test_a_session_refusal_is_never_retried(exc: Exception) -> None:
    failure = classify_tool_exception(exc)
    assert failure.failure_class == FailureClass.APPLICATION
    assert failure.code == "CONTROL"


def test_a_retry_or_breaker_layer_cannot_sit_on_the_shared_engine_call() -> None:
    from mas.runtime.driver.mocks import AutoCtxAssembler
    from mas.runtime.engine.llm_live import LiveLlmEngine

    engine = LiveLlmEngine(ctx=AutoCtxAssembler(), api_base="http://127.0.0.1:9/v1", api_key_env="K", model="m")
    with pytest.raises(ValueError, match="per: destination"):
        engine.install_infra_pipeline([{"layer": "retry", "per": "all", "for": ["llm"]}])


def test_the_transition_loop_outlasts_a_burst_of_conflicts() -> None:
    from mas.runtime.layers.state import MemorySpecPort, transition

    port = MemorySpecPort()
    assert transition(port, "x", "t", lambda s: {**s, "n": 1}, why="set")[1]["n"] == 1
