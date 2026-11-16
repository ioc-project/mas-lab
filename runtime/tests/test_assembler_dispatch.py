#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Kernel dispatcher → spec.assembler (smallest-kernel shrink)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from mas.library.standard.plugins.context.assembler import ContextAssemblerPlugin
from mas.library.standard.plugins.context.conversation import StackConversation
from mas.runtime.boundary.context.assemble import assemble_llm_messages
from mas.runtime.boundary.context.assembly_cache import (
    cached_assembler,
    cached_context_manager,
)
from mas.runtime.boundary.context.provider_invariant import (
    ProviderPayloadError,
    assert_provider_payload,
)
from mas.runtime.driver.mocks import AutoCtxAssembler
from mas.runtime.engine.llm_live import LiveLlmEngine


def _tool_turn(call_id: str, *, content: str = "result") -> list[dict[str, Any]]:
    return [
        {"role": "user", "content": f"ask-{call_id}"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": call_id, "function": {"name": "f", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": call_id, "content": content},
        {"role": "assistant", "content": "done"},
    ]


def test_default_assembler_is_context_assembler_plugin() -> None:
    ctx = AutoCtxAssembler(last_user_text="hi")
    plugin = cached_assembler(ctx, None)
    assert isinstance(plugin, ContextAssemblerPlugin)
    assert callable(plugin.assemble_messages)


def test_assembler_is_cached_per_ctx_and_manifest_identity() -> None:
    ctx = AutoCtxAssembler(last_user_text="hi")
    manifest = {"spec": {}}
    first = cached_assembler(ctx, manifest)
    second = cached_assembler(ctx, manifest)
    assert first is second
    other = cached_assembler(ctx, {"spec": {}})
    assert other is not first


async def test_dispatcher_rejects_plugin_without_assemble_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = AutoCtxAssembler(last_user_text="hi")
    monkeypatch.setattr(
        "mas.runtime.boundary.context.assemble.cached_assembler",
        lambda _ctx, _manifest: object(),
    )
    with pytest.raises(TypeError, match="assemble_messages"):
        await assemble_llm_messages(ctx)


async def test_kernel_asserts_pairing_if_plugin_returns_an_unpaired_payload() -> None:
    ctx = AutoCtxAssembler(last_user_text="q")
    plugin = cached_assembler(ctx, None)

    def unpaired(_ctx: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_1", "function": {"name": "f", "arguments": "{}"}}],
            },
        ]

    plugin.assemble_messages = unpaired  # type: ignore[method-assign]
    with pytest.raises(ProviderPayloadError, match="tool results"):
        await assemble_llm_messages(ctx)


async def test_engine_build_messages_does_not_call_on_pre_llm_call() -> None:
    """Live path is assemble_messages, not the legacy pre-LLM hook."""
    ctx = AutoCtxAssembler(last_user_text="hi")
    engine = LiveLlmEngine(ctx=ctx, manifest={"spec": {}})
    with patch.object(
        ContextAssemblerPlugin,
        "on_pre_llm_call",
        side_effect=AssertionError("on_pre_llm_call must not run on the engine path"),
    ):
        messages = await engine._abuild_messages()
    assert messages[-1]["content"] == "hi"
    assert_provider_payload(messages)


async def test_engine_build_messages_pairs_committed_history_and_live_wm() -> None:
    ctx = AutoCtxAssembler(last_user_text="follow-up")
    ctx.committed_messages = _tool_turn("call_old")
    ctx.record_assistant_tool_call(call_id="call_live", tool_name="search", arguments={})
    ctx.record_tool_result(call_id="call_live", content="now")
    engine = LiveLlmEngine(ctx=ctx, manifest={"spec": {"context_manager": {"type": "stack"}}})
    messages = await engine._abuild_messages()
    assert_provider_payload(messages)
    assert any(m.get("tool_call_id") == "call_old" for m in messages)
    assert any(m.get("tool_call_id") == "call_live" for m in messages)
    assert messages[-1]["role"] == "tool"


async def test_assemble_messages_uses_manifest_cm_not_plugin_ctor_strategy() -> None:
    """Engine assembly resolves CM from the manifest, not ContextAssemblerPlugin(conversation_strategy=...)."""
    plugin = ContextAssemblerPlugin(conversation_strategy=StackConversation(max_messages=1))
    past: list[dict[str, Any]] = []
    for i in range(4):
        past.extend(_tool_turn(f"call_{i}"))
    ctx = AutoCtxAssembler(last_user_text="now", committed_messages=past)
    messages = await plugin.assemble_messages(
        ctx,
        manifest={
            "spec": {
                "context_manager": {
                    "type": "stack",
                    "params": {"max_messages": 100, "trimmer": {"max_tokens": 500_000}},
                }
            }
        },
    )
    assert_provider_payload(messages)
    assert any(m.get("tool_call_id") == "call_0" for m in messages)


def test_spec_assembler_params_reach_the_assembler() -> None:
    ctx = AutoCtxAssembler(last_user_text="hi")
    manifest = {
        "spec": {
            "assembler": {
                "type": "assembler",
                "params": {"emit_segments": False},
            }
        }
    }
    plugin = cached_assembler(ctx, manifest)
    assert isinstance(plugin, ContextAssemblerPlugin)
    assert plugin._emit_segments is False


def test_spec_assembler_string_shorthand() -> None:
    ctx = AutoCtxAssembler(last_user_text="hi")
    plugin = cached_assembler(ctx, {"spec": {"assembler": "assembler"}})
    assert isinstance(plugin, ContextAssemblerPlugin)


def test_spec_assembler_unknown_type_fails() -> None:
    ctx = AutoCtxAssembler(last_user_text="hi")
    with pytest.raises(KeyError, match="spec.assembler"):
        cached_assembler(ctx, {"spec": {"assembler": {"type": "does-not-exist"}}})


def test_context_manager_cache_reuses_instance_for_hysteresis() -> None:
    ctx = AutoCtxAssembler(last_user_text="now")
    manifest = {"spec": {"context_manager": {"type": "summarising", "params": {"keep_turns": 2}}}}
    first = cached_context_manager(ctx, manifest)
    second = cached_context_manager(ctx, manifest)
    assert first is second


async def test_assembler_plugin_compacts_committed_history() -> None:
    ctx = AutoCtxAssembler(
        last_user_text="now",
        manifest={"spec": {"context_manager": {"type": "sliding_window", "params": {"window_size": 1}}}},
    )
    for i in range(3):
        ctx.note_user_input(f"u{i}")
        await ctx.note_agent_response(f"a{i}")
    turns = [m["content"] for m in ctx.committed_messages if m.get("role") == "user"]
    assert turns == ["u2"]
    plugin = cached_assembler(ctx, ctx.manifest)
    assert callable(getattr(plugin, "compact_committed_history", None))
