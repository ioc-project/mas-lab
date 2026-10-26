#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Assembly boundary: provider-safe history + structurally pinned working memory."""

from __future__ import annotations

from typing import Any

from mas.library.standard.lib.context.working_memory import (
    bounded_working_memory_tail,
    working_memory_slice_limit,
)
from mas.library.standard.plugins.context.assembler import ContextAssemblerPlugin
from mas.library.standard.plugins.context.conversation import StackConversation
from mas.runtime.boundary.context.assemble import assemble_llm_messages
from mas.runtime.boundary.context.provider_invariant import assert_provider_payload
from mas.runtime.driver.mocks import AutoCtxAssembler
from mas.runtime.kernel.inflight import register_inflight
from mas.runtime.kernel.outbound_waits import pending_outbound_waits
from mas.runtime.kernel.state import QProduct


def _committed_tool_turn(call_id: str, answer: str) -> list[dict[str, Any]]:
    return [
        {"role": "user", "content": f"question-{call_id}"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": call_id, "function": {"name": "search", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": call_id, "content": f"result-{call_id}"},
        {"role": "assistant", "content": answer},
    ]


def test_plain_turn_history_unchanged() -> None:
    ctx = AutoCtxAssembler(last_user_text="Follow-up?")
    ctx.turn_history = [("Q1?", "A1."), ("Q2?", "A2.")]
    messages = assemble_llm_messages(ctx)
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant", "user"]
    assert messages[-1]["content"] == "Follow-up?"


def test_working_memory_pinned_whole_in_turn() -> None:
    q = QProduct()
    register_inflight(q, 7, kind="TOOL", op="TOOL_CALL")
    ctx = AutoCtxAssembler(last_user_text="continue")
    ctx.record_assistant_tool_call(call_id="call_7", tool_name="search", arguments={})
    ctx.record_tool_result(call_id="call_7", content="partial")
    ctx.record_assistant_tool_call(call_id="call_8", tool_name="search", arguments={})

    assert len(ctx.working_memory.messages) == 3
    assert pending_outbound_waits(q)[0].kind == "TOOL"


def test_assemble_pins_wm_under_budget() -> None:
    ctx = AutoCtxAssembler(last_user_text="Who is POTUS?")
    ctx.record_assistant_tool_call(call_id="call_1", tool_name="web-search", arguments={"q": "POTUS"})
    ctx.record_tool_result(call_id="call_1", content="Donald Trump is president.")
    manifest = {
        "spec": {
            "context_manager": {
                "type": "stack",
                "params": {"trimmer": {"max_tokens": 50, "reserve_tokens": 0}},
            },
        }
    }
    messages = assemble_llm_messages(ctx, manifest=manifest)
    assert messages[-1]["role"] == "tool"
    assert_provider_payload(messages)


def test_assemble_committed_history_provider_safe_after_stack_trim() -> None:
    committed: list[dict[str, Any]] = []
    for i in range(8):
        committed.extend(_committed_tool_turn(f"call_{i}", f"answer-{i}"))
    ctx = AutoCtxAssembler(last_user_text="What next?", committed_messages=committed)
    manifest = {
        "spec": {
            "context_manager": {
                "type": "stack",
                "params": {"max_messages": 6, "trimmer": {"max_tokens": 500_000}},
            },
        }
    }
    messages = assemble_llm_messages(ctx, manifest=manifest)
    assert messages[-1]["content"] == "What next?"
    assert_provider_payload(messages)


def test_inflight_partial_parallel_tools_filled_before_provider() -> None:
    """A live parallel group with one result still outstanding: final assemble
    inserts an empty tool row so Bedrock sees 2 toolUse + 2 toolResult.
    """
    ctx = AutoCtxAssembler(last_user_text="continue")
    ctx.working_memory.record_assistant_tool_calls(
        [
            ("call_a", "search", {"q": "a"}),
            ("call_b", "search", {"q": "b"}),
        ]
    )
    ctx.working_memory.record_tool_result(call_id="call_a", content="result-a")
    messages = assemble_llm_messages(ctx)
    assert_provider_payload(messages)
    asst = [m for m in messages if m.get("tool_calls")][-1]
    assert [c["id"] for c in asst["tool_calls"]] == ["call_a", "call_b"]
    tools = [m for m in messages if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in tools] == ["call_a", "call_b"]
    assert tools[-1]["content"] == ""


def test_multi_turn_committed_tool_visible_on_follow_up() -> None:
    ctx = AutoCtxAssembler()
    ctx.note_user_input("Who is POTUS?")
    ctx.record_assistant_tool_call(call_id="call_1", tool_name="web-search", arguments={"q": "POTUS"})
    ctx.record_tool_result(call_id="call_1", content="Donald Trump")
    ctx.note_agent_response("Donald Trump is POTUS.")
    ctx.note_user_input("Who was before?")
    messages = assemble_llm_messages(ctx)
    assert any(m.get("role") == "tool" for m in messages)
    assert messages[-1]["content"] == "Who was before?"


def test_hitl_pause_fold_completes_parallel_group_from_working_memory() -> None:
    """HITL pause commits the assistant + first result; the remaining result
    lands in working memory. Assembly must concat then pair — not strip the
    assistant and leave extra toolResults after the previous turn.
    """
    ctx = AutoCtxAssembler(last_user_text="")
    ctx.committed_messages = [
        {"role": "user", "content": "research both"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_a", "function": {"name": "search", "arguments": "{}"}},
                {"id": "call_b", "function": {"name": "search", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_a", "content": "result-a"},
    ]
    ctx.working_memory.record_tool_result(call_id="call_b", content="result-b")
    messages = assemble_llm_messages(ctx)
    assert_provider_payload(messages)
    tools = [m for m in messages if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in tools] == ["call_a", "call_b"]
    asst = [m for m in messages if m.get("tool_calls")]
    assert len(asst) == 1
    assert [c["id"] for c in asst[0]["tool_calls"]] == ["call_a", "call_b"]


def test_assemble_drops_duplicate_tool_results_before_provider() -> None:
    ctx = AutoCtxAssembler(last_user_text="q")
    ctx.working_memory.record_assistant_tool_calls(
        [("call_a", "search", {"q": "a"}), ("call_b", "search", {"q": "b"})]
    )
    ctx.working_memory.record_tool_result(call_id="call_a", content="ra")
    ctx.working_memory.record_tool_result(call_id="call_b", content="rb")
    ctx.working_memory.record_tool_result(call_id="call_a", content="ra-dup")
    messages = assemble_llm_messages(ctx)
    assert_provider_payload(messages)
    assert [m["tool_call_id"] for m in messages if m.get("role") == "tool"] == ["call_a", "call_b"]


def test_hitl_style_mismatched_ids_rebound_in_assembly() -> None:
    ctx = AutoCtxAssembler(
        last_user_text="Follow-up?",
        committed_messages=[
            {"role": "user", "content": "Who is POTUS?"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_1", "function": {"name": "search", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "call_3", "content": "Maxence Postu is POTUS"},
        ],
    )
    messages = assemble_llm_messages(ctx)
    assert any("Maxence Postu is POTUS" in str(m.get("content")) for m in messages)
    assert_provider_payload(messages)


def test_assembler_plugin_retains_tool_messages() -> None:
    plugin = ContextAssemblerPlugin(conversation_strategy=StackConversation(max_messages=4))
    messages = _committed_tool_turn("call_0", "a0") + _committed_tool_turn("call_1", "a1")
    messages.append({"role": "user", "content": "current"})
    out = plugin._apply_conversation_strategy(messages)
    assert any(m.get("role") == "tool" for m in out)
    assert_provider_payload(out)


def test_assembler_preserves_live_turn_inflight_tool_calls() -> None:
    plugin = ContextAssemblerPlugin(conversation_strategy=StackConversation(max_messages=4))
    messages = _committed_tool_turn("call_0", "a0") + [
        {"role": "user", "content": "current"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call_live", "function": {"name": "search", "arguments": "{}"}}],
        },
    ]
    out = plugin._apply_conversation_strategy(messages)
    live = [m for m in out if m.get("tool_calls")]
    assert any(c["id"] == "call_live" for m in live for c in m["tool_calls"])
    assert_provider_payload(out)


def test_high_level_react_path_still_sees_tool_result() -> None:
    ctx = AutoCtxAssembler(last_user_text="Who is POTUS?")
    ctx.record_assistant_tool_call(call_id="call_1", tool_name="web-search", arguments={"q": "POTUS"})
    ctx.record_tool_result(call_id="call_1", content="Donald Trump is president.")
    messages = assemble_llm_messages(ctx)
    assert messages[-1]["role"] == "tool"
    assert messages[-1]["content"] == "Donald Trump is president."


def _record_malformed_get_metrics_retry(ctx: AutoCtxAssembler, n: int) -> None:
    cid = f"call_{n}"
    ctx.record_assistant_tool_call(
        call_id=cid, tool_name="get_metrics", arguments={"payment_db": None, "window": "15m"}
    )
    ctx.record_tool_result(
        call_id=cid,
        content='{"error": "required parameter \'service\' is missing"}',
    )


def test_issue_65_stuck_retry_loop_ages_out_under_token_budget() -> None:
    """A stuck retry loop is kept by default; the token trimmer ages oldest groups."""
    ctx = AutoCtxAssembler(last_user_text="plan a weekend trip to Celestia")
    for n in range(1, 16):
        _record_malformed_get_metrics_retry(ctx, n)
    assert len(ctx.working_memory.messages) == 30

    unbounded = assemble_llm_messages(ctx)
    unbounded_tool_msgs = [m for m in unbounded if m.get("role") == "tool"]
    assert len(unbounded_tool_msgs) == 15
    assert_provider_payload(unbounded)

    tight = assemble_llm_messages(
        ctx,
        manifest={
            "spec": {
                "context_manager": {
                    "params": {"trimmer": {"max_tokens": 80, "reserve_tokens": 0, "safety_margin_ratio": 0}}
                }
            }
        },
    )
    tight_tool_msgs = [m for m in tight if m.get("role") == "tool"]
    assert len(tight_tool_msgs) < 15
    assert tight_tool_msgs[-1]["tool_call_id"] == "call_15"
    assert_provider_payload(tight)


def test_working_memory_messages_is_ignored() -> None:
    """Deprecated count cap is accepted in YAML and has no effect."""
    ctx = AutoCtxAssembler(last_user_text="plan a weekend trip to Celestia")
    for n in range(1, 6):
        _record_malformed_get_metrics_retry(ctx, n)
    manifest = {
        "spec": {"context_manager": {"type": "sliding_window", "params": {"working_memory_messages": 4}}}
    }
    messages = assemble_llm_messages(ctx, manifest=manifest)
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 5
    assert_provider_payload(messages)


def test_working_memory_slice_limit_is_unbounded() -> None:
    assert working_memory_slice_limit(None) == 0
    assert working_memory_slice_limit({"spec": {"context_manager": {"params": {"working_memory_messages": 3}}}}) == 0
    assert working_memory_slice_limit({"spec": {"context_manager": ["not", "a", "binding"]}}) == 0
    assert working_memory_slice_limit({"spec": {"context_manager": 42}}) == 0


def test_bounded_working_memory_tail_never_splits_tool_group() -> None:
    messages = []
    for n in range(5):
        messages.append(
            {"role": "assistant", "content": "", "tool_calls": [{"id": f"c{n}", "function": {"name": "t"}}]}
        )
        messages.append({"role": "tool", "tool_call_id": f"c{n}", "content": "ok"})
    out = bounded_working_memory_tail(messages, 3)
    assert out[0]["role"] == "assistant"  # never starts mid tool-result
    assert out[0]["tool_calls"][0]["id"] == "c3"


def test_concurrent_sessions_have_isolated_kernel_and_assembly() -> None:
    """Each session owns its own kernel state and context; outbound waits do not cross sessions."""
    q_a = QProduct()
    q_b = QProduct()
    register_inflight(q_a, 1, kind="TOOL", op="TOOL_CALL")

    ctx_a = AutoCtxAssembler(session_id="sess-a", last_user_text="A")
    ctx_a.record_assistant_tool_call(call_id="call_a", tool_name="search", arguments={})
    ctx_a.record_tool_result(call_id="call_a", content="result-a")

    ctx_b = AutoCtxAssembler(session_id="sess-b", last_user_text="B")

    msgs_a = assemble_llm_messages(ctx_a)
    msgs_b = assemble_llm_messages(ctx_b)

    assert pending_outbound_waits(q_a)
    assert not pending_outbound_waits(q_b)
    assert any(m.get("role") == "tool" for m in msgs_a)
    assert msgs_b[-1]["content"] == "B"
    assert not any(m.get("role") == "tool" for m in msgs_b)
    assert_provider_payload(msgs_a)
    assert_provider_payload(msgs_b)
