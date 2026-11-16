#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Working memory + registry-backed context assembly (kernel path)."""

from __future__ import annotations

from unittest.mock import MagicMock

from mas.library.standard.plugins.context.token_budget import trim_messages_to_budget
from mas.runtime.boundary.context.assemble import assemble_llm_messages
from mas.runtime.boundary.context.working_memory import (
    SOURCE_TYPE,
    WorkingMemoryStore,
)
from mas.runtime.contracts.cm_factory import CMFactory
from mas.runtime.driver.driver import DriverTrace, KernelDriver
from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.driver.mocks import AutoCtxAssembler
from mas.runtime.engine.llm_live import LiveLlmEngine
from mas.runtime.kernel.run_event import from_engine_return
from mas.runtime.kernel.state import QProduct, RunEvent, RunLedger
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn, ToolCallSpec


def _tool_event(cid: int, text: str) -> RunEvent:
    return RunEvent(correlation_id=cid, response_kind="TOOL_RESULT", next_step="LLM_CALL", text=text)


def _llm_tool_call(cid: int, tool: str, arguments: dict | None = None) -> EngineIoReturn:
    return EngineIoReturn(
        correlation_id=cid,
        response_kind="MODEL_TEXT",
        next_step="TOOL_CALL",
        tool_name=tool,
        tool_arguments=dict(arguments or {}),
        text="",
    )


def _copy(driver: KernelDriver, *rows: RunEvent | EngineIoReturn) -> None:
    events = [from_engine_return(row) if isinstance(row, EngineIoReturn) else row for row in rows]
    driver._project_log_events(events)


def test_working_memory_source_type():
    assert SOURCE_TYPE == "working_memory"


def test_default_cm_is_summarising():
    cm = CMFactory.create(manifest={})
    assert cm.__class__.__name__ == "SummarizingConversation"
    assert cm.keep_turns == 10
    assert cm.hysteresis_ratio == 0.1
    assert cm._summarizer is not None
    assert cm._summarizer.__class__.__name__ == "LlmSummarizer"


async def test_cm_assembly_includes_working_memory_after_user():
    ctx = AutoCtxAssembler(last_user_text="Who is POTUS?")
    ctx.record_assistant_tool_call(call_id="call_1", tool_name="web-search", arguments={"q": "POTUS"})
    ctx.record_tool_result(call_id="call_1", content="Donald Trump is president.")
    messages = await assemble_llm_messages(ctx)
    assert [m["role"] for m in messages] == ["user", "assistant", "tool"]


async def test_build_messages_includes_tool_results():
    ctx = AutoCtxAssembler(last_user_text="Who is current POTUS?")
    ctx.record_assistant_tool_call(call_id="call_1", tool_name="web-search", arguments={"q": "POTUS"})
    ctx.record_tool_result(call_id="call_1", content="Donald Trump is president.")
    engine = LiveLlmEngine(ctx=ctx, manifest={"spec": {"tools": ["web-search"]}})
    msgs = await engine._abuild_messages()
    assert any(m["role"] == "tool" for m in msgs)
    assert msgs[-1]["role"] == "tool"


def test_driver_records_working_memory():
    ctx = AutoCtxAssembler()
    driver = KernelDriver(kernel=MagicMock(), engine=MagicMock(), ctx=ctx)
    _copy(driver, _llm_tool_call(7, "web-search", {"query": "POTUS"}))
    assert ctx.working_memory.messages[0]["tool_calls"][0]["id"] == "call_7"


def test_engine_return_does_not_write_transcript():
    """The engine return is not an accepted fact yet. SKIP/BLOCK must not leak."""
    ctx = AutoCtxAssembler()
    driver = KernelDriver(kernel=MagicMock(), engine=MagicMock(), ctx=ctx)
    driver._record_engine_return(
        DriverTrace(),
        InvokeEngineIo(correlation_id=7, op="LLM_CALL"),
        _llm_tool_call(7, "web-search", {"query": "POTUS"}),
    )
    assert ctx.working_memory.messages == []


def test_driver_records_parallel_tool_calls_on_llm_return():
    ctx = AutoCtxAssembler()
    driver = KernelDriver(kernel=MagicMock(), engine=MagicMock(), ctx=ctx)
    _copy(
        driver,
        EngineIoReturn(
            correlation_id=4,
            response_kind="MODEL_TEXT",
            next_step="PARALLEL_TOOL_CALLS",
            parallel_tools=(
                ToolCallSpec(tool_name="web-search", tool_arguments={"query": "POTUS"}),
                ToolCallSpec(tool_name="calc", tool_arguments={"expression": "1+1"}),
            ),
            text="",
        ),
    )
    calls = ctx.working_memory.messages[0]["tool_calls"]
    assert [c["id"] for c in calls] == ["call_4_0", "call_4_1"]
    _copy(driver, _tool_event(99, "search hits"))
    assert ctx.working_memory.messages[-1]["role"] == "tool"
    assert ctx.working_memory.messages[-1]["tool_call_id"] == "call_4_0"


def test_hitl_allow_does_not_record_tool_result_before_tool_runs():
    """Regression: tool-call HITL ALLOW used to append an empty tool row, then
    the real TOOL_RESULT invented a second id Azure rejects."""
    ctx = AutoCtxAssembler()
    driver = KernelDriver(kernel=MagicMock(), engine=MagicMock(), ctx=ctx)
    _copy(driver, _llm_tool_call(1, "web-search", {"query": "POTUS"}))
    _copy(driver)
    assert [m["role"] for m in ctx.working_memory.messages] == ["assistant"]
    _copy(driver, _tool_event(3, "search hits"))
    tool_msgs = [m for m in ctx.working_memory.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "call_1"
    assert tool_msgs[0]["content"] == "search hits"


def test_parallel_tool_calls_single_assistant_message():
    store = WorkingMemoryStore()
    store.record_assistant_tool_calls(
        [("call_10", "web-search", {"q": "a"}), ("call_11", "calculator", {"expression": "1+1"})]
    )
    assert len(store.messages[0]["tool_calls"]) == 2


def test_token_budget_trimmer():
    messages = [{"role": "system", "content": "x" * 50}]
    messages.extend({"role": "user", "content": "y" * 3000} for _ in range(10))
    trimmed = trim_messages_to_budget(messages, max_tokens=200, reserve_tokens=50)
    assert len(trimmed) < len(messages)


def test_tool_result_binds_to_llm_call_id_not_tool_egress_id():
    """Regression: tool egress correlation_id differs from LLM correlation_id."""
    ctx = AutoCtxAssembler()
    driver = KernelDriver(kernel=MagicMock(), engine=MagicMock(), ctx=ctx)
    _copy(driver, _llm_tool_call(3, "web-search", {"query": "POTUS"}))
    _copy(driver, _tool_event(2, "Donald Trump is president."))
    assistant = ctx.working_memory.messages[0]
    tool_msg = ctx.working_memory.messages[1]
    assert assistant["tool_calls"][0]["id"] == "call_3"
    assert tool_msg["tool_call_id"] == "call_3"


def test_react_loop_second_llm_sees_tool_result():
    ctx = AutoCtxAssembler()
    rounds = {"n": 0}

    class ScriptEngine:
        def exchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
            return op

        def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
            if io.op == "LLM_CALL":
                rounds["n"] += 1
                if rounds["n"] == 1:
                    return EngineIoReturn(
                        correlation_id=io.correlation_id,
                        response_kind="MODEL_TEXT",
                        next_step="TOOL_CALL",
                        tool_name="web-search",
                        tool_arguments={"query": "POTUS"},
                        text="",
                    )
                assert any(m.get("role") == "tool" for m in ctx.working_memory.messages)
                return EngineIoReturn(
                    correlation_id=io.correlation_id,
                    response_kind="MODEL_TEXT",
                    next_step="STOP",
                    text="Donald Trump is the current President.",
                )
            return EngineIoReturn(
                correlation_id=io.correlation_id,
                response_kind="TOOL_RESULT",
                next_step="LLM_CALL",
                text="results",
            )

        def set_scheduled_tool(self, name: str, arguments: dict | None = None) -> None:
            pass

    RuntimeInstance.from_parts(ctx=ctx, engine=ScriptEngine()).run_user_text(  # type: ignore[arg-type]
        "Who is current POTUS?",
    )
    assert rounds["n"] == 2


def test_new_run_events_uses_seq_not_list_tail():
    """append_inflight re-sorts by cid; seq still marks the new row."""
    run = RunLedger()
    run.append(_tool_event(1, "a"))
    run.append(_tool_event(2, "b"))
    run.append(_tool_event(4, "d"))
    seen = run.max_seq()
    run.append_inflight(_tool_event(3, "c"))
    delta = run.since(seen)
    assert [(row.correlation_id, row.text, row.seq) for row in delta] == [(3, "c", seen + 1)]
    assert [row.correlation_id for row in run.events[len(run.events) - 1 :]] == [4]


def test_project_event_copies_model_text_then_tool_result():
    store = WorkingMemoryStore()
    assistant = RunEvent(
        correlation_id=3,
        response_kind="MODEL_TEXT",
        next_step="TOOL_CALL",
        tool_name="activate_skill",
        tool_arguments={"name": "x"},
    )
    assert store.project_event(assistant) == "call_3"
    assert store.project_event(assistant) is None
    result = _tool_event(4, "skill-body")
    assert store.project_event(result) == "call_3"
    assert store.messages[-1]["content"] == "skill-body"


def test_project_tool_result_allows_empty_skip_text():
    store = WorkingMemoryStore()
    store.record_assistant_tool_call(call_id="call_5", tool_name="web-search", arguments={})
    assert store.project_tool_result(cid=7, content="") == "call_5"
    assert store.messages[-1]["content"] == ""
    assert store.is_synced(7)
    assert store.project_tool_result(cid=7, content="late") is None


def test_kernel_skip_of_tool_result_copies_empty_line() -> None:
    """SKIP through the kernel, then the driver copier, before the next LLM."""
    from unittest.mock import patch

    from mas.runtime.boundary.gov.ingress_plugin import IngressGovDecision
    from mas.runtime.kernel.config import KernelConfig
    from mas.runtime.kernel.ingress_step import apply_engine_io_return
    from mas.runtime.kernel.state import QProduct, ToolState
    from mas.runtime.schema.egress import NoOp
    from mas.runtime.schema.governance import GovernanceAction

    ctx = AutoCtxAssembler()
    ctx.working_memory.record_assistant_tool_call(call_id="call_7", tool_name="web-search", arguments={})
    q = QProduct()
    q.pending_engine_correlation_id = 7
    q.tool = ToolState.EXECUTING
    run = RunLedger()
    event = EngineIoReturn(
        correlation_id=7,
        response_kind="TOOL_RESULT",
        next_step="LLM_CALL",
        text="secret-result",
    )
    with (
        patch(
            "mas.runtime.kernel.ingress_step.ingress_governance_valid",
            return_value=True,
        ),
        patch(
            "mas.runtime.kernel.ingress_step.run_ingress_validate_envelope",
            return_value=IngressGovDecision(action=GovernanceAction.SKIP),
        ),
    ):
        apply_engine_io_return(q, run, event, config=KernelConfig(), evaluate=MagicMock(return_value=[NoOp()]))
    kernel = MagicMock()
    kernel.run = run
    driver = KernelDriver(kernel=kernel, engine=MagicMock(), ctx=ctx)
    driver._after_kernel_step(event, 0)
    tool_msgs = [m for m in ctx.working_memory.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["content"] == ""
    assert tool_msgs[0]["tool_call_id"] == "call_7"


def test_hitl_skip_of_tool_that_never_ran_copies_from_kernel() -> None:
    from mas.runtime.kernel.config import KernelConfig
    from mas.runtime.kernel.hitl_step import apply_hitl_resolve
    from mas.runtime.machines.gov import gov_enter_hitl_pending
    from mas.runtime.schema.egress import NoOp
    from mas.runtime.schema.hitl import HitlResolveChoice
    from mas.runtime.schema.ingress import HitlResolve

    ctx = AutoCtxAssembler()
    ctx.working_memory.record_assistant_tool_call(call_id="call_5", tool_name="web-search", arguments={})
    run = RunLedger()
    run.append(
        RunEvent(
            correlation_id=5,
            response_kind="MODEL_TEXT",
            next_step="TOOL_CALL",
            tool_name="web-search",
        )
    )
    seen = run.max_seq()
    q = QProduct()
    q.pending_tool_name = "web-search"
    gov_enter_hitl_pending(q, request_id=11, pending_schedule="TOOL_CALL")
    apply_hitl_resolve(
        q,
        run,
        HitlResolve(request_id=11, resolution=HitlResolveChoice.SKIP),
        config=KernelConfig(),
        evaluate=MagicMock(return_value=[NoOp()]),
    )
    kernel = MagicMock()
    kernel.run = run
    driver = KernelDriver(kernel=kernel, engine=MagicMock(), ctx=ctx)
    driver._after_kernel_step(MagicMock(), seen)
    tool_msgs = [m for m in ctx.working_memory.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert "skipped" in tool_msgs[0]["content"]
    assert tool_msgs[0]["tool_call_id"] == "call_5"


def test_tool_result_binds_after_hitl_fold_without_rewriting_assistant() -> None:
    """After HITL pause the assistant row lives in history, not working memory."""
    ctx = AutoCtxAssembler()
    ctx.committed_messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_4_0",
                    "type": "function",
                    "function": {"name": "web-search", "arguments": "{}"},
                }
            ],
        }
    ]
    run = RunLedger()
    run.append(_tool_event(10, "hits"))
    kernel = MagicMock()
    kernel.run = run
    driver = KernelDriver(kernel=kernel, engine=MagicMock(), ctx=ctx)
    driver._after_kernel_step(MagicMock(), 0)
    assert [m["role"] for m in ctx.working_memory.messages] == ["tool"]
    assert ctx.working_memory.messages[-1]["tool_call_id"] == "call_4_0"
    assert "call_10" not in str(ctx.working_memory.messages)


def _first_unsynced_tool_result(events, synced: set[int]):
    """The old copier: first leftover TOOL_RESULT in the whole session log."""
    for row in events:
        if getattr(row, "response_kind", "") != "TOOL_RESULT":
            continue
        cid = int(getattr(row, "correlation_id", 0) or 0)
        if cid and cid not in synced:
            return row
    return None


def test_turn_reset_does_not_bind_prior_turn_tool_result():
    """The activate_skill cascade: search-the-log after a turn reset.

    Turn 1 leaves a tool result on the run log. The transcript is emptied
    at the turn boundary; the log is not. Searching the log for any
    leftover tool result pasted result-1 into turn 2's new activate_skill
    slot, so the real skill body arrived one call late.

    The copier is ``run.since(snapshot)`` then ``store.project_new``. The
    leftover row is still on the log; it is not in the snapshot delta.
    """
    run = RunLedger()
    store = WorkingMemoryStore()

    run.append(from_engine_return(_llm_tool_call(1, "web-search")))
    store.project_event(run.latest())
    run.append(_tool_event(2, "result-1"))
    store.project_event(run.latest())
    assert store.messages[-1]["content"] == "result-1"

    store.clear()
    leftover = _first_unsynced_tool_result(run.events, store._synced_tool_result_cids)
    assert leftover is not None and leftover.text == "result-1"

    seen = run.max_seq()
    run.append(from_engine_return(_llm_tool_call(3, "activate_skill", {"name": "my-skill"})))
    store.project_new(run.since(seen))
    assert [m["role"] for m in store.messages] == ["assistant"]
    assert store.messages[0]["tool_calls"][0]["function"]["name"] == "activate_skill"
    assert all(m.get("content") != "result-1" for m in store.messages)

    seen = run.max_seq()
    run.append(_tool_event(4, "skill-body"))
    store.project_new(run.since(seen))
    tool_msgs = [m for m in store.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["content"] == "skill-body"
    assert tool_msgs[0]["tool_call_id"] == "call_3"


def test_follow_up_turn_binds_fresh_tool_result_not_prior_ledger():
    """Production loop for the activate_skill cascade.

    Turn 1 runs web-search → result-1. Turn 2 runs activate_skill.
    The follow-up LLM on turn 2 must see result-2, not result-1.
    """
    ctx = AutoCtxAssembler()
    seen: list[str] = []

    class ScriptEngine:
        def __init__(self) -> None:
            self.llm_n = 0
            self.tool_n = 0

        def exchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
            return op

        def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
            if io.op == "LLM_CALL":
                self.llm_n += 1
                if self.llm_n % 2 == 1:
                    if self.llm_n == 1:
                        name, arguments = "web-search", {"query": "q1"}
                    else:
                        name, arguments = "activate_skill", {"name": "my-skill"}
                    return EngineIoReturn(
                        correlation_id=io.correlation_id,
                        response_kind="MODEL_TEXT",
                        next_step="TOOL_CALL",
                        tool_name=name,
                        tool_arguments=arguments,
                        text="",
                    )
                tool_msgs = [m for m in ctx.working_memory.messages if m.get("role") == "tool"]
                seen.append(tool_msgs[-1]["content"] if tool_msgs else "")
                return EngineIoReturn(
                    correlation_id=io.correlation_id,
                    response_kind="MODEL_TEXT",
                    next_step="STOP",
                    text=f"answer-{self.llm_n}",
                )
            self.tool_n += 1
            return EngineIoReturn(
                correlation_id=io.correlation_id,
                response_kind="TOOL_RESULT",
                next_step="LLM_CALL",
                text=f"result-{self.tool_n}",
            )

        def set_scheduled_tool(self, name: str, arguments: dict | None = None) -> None:
            pass

    inst = RuntimeInstance.from_parts(ctx=ctx, engine=ScriptEngine())  # type: ignore[arg-type]
    inst.run_user_text("first", turn_id="u1")
    inst.run_user_text("second", turn_id="u2")
    assert seen == ["result-1", "result-2"]


def test_after_kernel_step_copies_only_rows_stamped_this_step():
    ctx = AutoCtxAssembler()
    run = RunLedger()
    run.append(_tool_event(1, "stale"))
    kernel = MagicMock()
    kernel.run = run
    driver = KernelDriver(kernel=kernel, engine=MagicMock(), ctx=ctx)
    _copy(driver, _llm_tool_call(3, "activate_skill", {"name": "my-skill"}))
    seen = run.max_seq()
    run.append(_tool_event(4, "real-skill-content"))
    driver._after_kernel_step(MagicMock(), seen)
    tool_msgs = [m for m in ctx.working_memory.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["content"] == "real-skill-content"


def test_hitl_skip_synthetic_result_projects_from_delta():
    """HITL skip writes a tool result under a fresh id on resolve.

    Copy must use the new log rows, not 'the current event is a tool
    result', or the skip text never lands before the follow-up LLM call.
    """
    ctx = AutoCtxAssembler()
    driver = KernelDriver(kernel=MagicMock(), engine=MagicMock(), ctx=ctx)
    _copy(driver, _llm_tool_call(5, "web-search"))
    _copy(driver, _tool_event(9, "Operator skipped tool"))
    tool_msgs = [m for m in ctx.working_memory.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["content"] == "Operator skipped tool"
    assert tool_msgs[0]["tool_call_id"] == "call_5"


async def test_turn_commit_preserves_tool_trajectory_for_follow_up():
    ctx = AutoCtxAssembler()
    ctx.note_user_input("Who is POTUS?")
    ctx.record_assistant_tool_call(call_id="call_1", tool_name="web-search", arguments={"query": "POTUS"})
    ctx.record_tool_result(call_id="call_1", content='[{"body": "Donald Trump is the 47th president."}]')
    ctx.record_assistant_message("Donald Trump is the current President.")
    await ctx.note_agent_response("Donald Trump is the current President.")

    ctx.note_user_input("Why did you say Biden?")
    messages = await assemble_llm_messages(ctx)
    roles = [m["role"] for m in messages]
    assert roles.count("tool") == 1
    assert "assistant" in roles
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "Why did you say Biden?"
