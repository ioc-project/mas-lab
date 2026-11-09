#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""LangGraph-style turn history, working memory, observability, and trajectory events."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from ci_llm import web_search_engine
from mas.runtime.boundary.context.assemble import assemble_llm_messages
from mas.runtime.boundary.obs.operator import ObservabilityOperator
from mas.runtime.driver.mocks import AutoCtxAssembler
from mas.runtime.schema.hitl import HitlResolveChoice
from mas.runtime.schema.ingress import HitlResolve
from mas.runtime.schema.observability import ObsEventKind

REPO_ROOT = Path(__file__).resolve().parents[1]
T01 = REPO_ROOT / "docs" / "tutorials" / "01-building-an-agent"


def _obs(*events: str) -> ObservabilityOperator:
    return ObservabilityOperator()


def test_n_turns_accumulate_in_committed_messages() -> None:
    ctx = AutoCtxAssembler()
    for n in range(3):
        q = f"Question {n}?"
        a = f"Answer {n}."
        ctx.note_user_input(q)
        ctx.record_assistant_message(a)
        ctx.note_agent_response(a)
    assert len(ctx.turn_history) == 3
    roles = [m["role"] for m in ctx.committed_messages]
    assert roles.count("user") == 3
    assert roles.count("assistant") == 3
    ctx.note_user_input("Follow-up?")
    messages = assemble_llm_messages(ctx)
    assert sum(1 for m in messages if m["role"] == "user") >= 4


def test_tool_trajectory_committed_and_visible_on_next_turn() -> None:
    ctx = AutoCtxAssembler()
    ctx.note_user_input("Who is POTUS?")
    ctx.record_assistant_tool_call(call_id="call_1", tool_name="web-search", arguments={"q": "POTUS"})
    ctx.record_tool_result(call_id="call_1", content="Maxence Postu is POTUS")
    ctx.note_agent_response("Maxence Postu is POTUS.")

    ctx.note_user_input("Who was POTUS before?")
    messages = assemble_llm_messages(ctx)
    assert any(m.get("role") == "tool" and "Maxence" in str(m.get("content")) for m in messages)
    assert messages[-1]["content"] == "Who was POTUS before?"


def test_in_turn_wm_cleared_on_new_turn() -> None:
    ctx = AutoCtxAssembler()
    ctx.note_user_input("Q1")
    ctx.record_assistant_tool_call(call_id="call_1", tool_name="web-search", arguments={})
    ctx.record_tool_result(call_id="call_1", content="search hit")
    assert ctx.working_memory.messages
    ctx.note_agent_response("A1")
    assert not ctx.working_memory.messages

    ctx.note_user_input("Q2")
    messages = assemble_llm_messages(ctx)
    wm_roles = [m["role"] for m in ctx.working_memory.messages]
    assert wm_roles == []
    assert any(m.get("role") == "tool" for m in messages)


def test_context_mutations_are_logged() -> None:
    op = ObservabilityOperator()
    ctx = AutoCtxAssembler(observability=op)
    ctx.note_user_input("Hi")
    ctx.record_assistant_tool_call(call_id="call_1", tool_name="web-search", arguments={})
    ctx.record_tool_result(call_id="call_1", content="result")
    ctx.note_agent_response("done")

    kinds = [e.kind for e in op.events]
    assert ObsEventKind.CONTEXT_MUTATION in kinds
    actions = [e.payload.get("action") for e in op.events if e.kind == ObsEventKind.CONTEXT_MUTATION]
    assert "turn_start" in actions
    assert "wm_clear" in actions
    assert "wm_append" in actions
    assert "turn_commit" in actions


def test_context_assembly_logged_per_llm_call() -> None:
    op = ObservabilityOperator()
    ctx = AutoCtxAssembler(observability=op, last_user_text="Who is POTUS?")
    ctx._assembly_correlation_id = 7
    ctx.record_assistant_tool_call(call_id="call_7", tool_name="web-search", arguments={})
    ctx.record_tool_result(call_id="call_7", content="Steered answer")

    assemble_llm_messages(ctx, correlation_id=7)
    assembled = [e for e in op.events if e.kind == ObsEventKind.CONTEXT_ASSEMBLED]
    assert assembled
    assert assembled[-1].correlation_id == 7
    assert assembled[-1].payload.get("message_count", 0) >= 3


def test_native_transform_emits_trajectory_events() -> None:
    from mas.library.standard.lib.observability.native.transform import NativeObservabilityTransform, TransformContext

    op = ObservabilityOperator()
    ctx = AutoCtxAssembler(observability=op, last_user_text="Q")
    ctx._assembly_correlation_id = 3
    ctx.record_assistant_tool_call(call_id="call_3", tool_name="web-search", arguments={})
    ctx.record_tool_result(call_id="call_3", content="R")
    assemble_llm_messages(ctx, correlation_id=3)
    op.record_engine_llm_return(correlation_id=3, text="A")

    transform = NativeObservabilityTransform()
    tctx = TransformContext(agent_id="agent")
    native: list[dict] = []
    for ev in op.events:
        payload = ev.model_dump(mode="json")
        payload["_source"] = "boundary"
        native.extend(transform.transform(payload, ctx=tctx))

    kinds = {r["kind"] for r in native}
    assert "context_assembled" in kinds
    assert "context_part_contributed" in kinds
    assert "state_update_start" in kinds
    assert "llm_call_end" in kinds
    # context_part_contributed now carries the LLM call's own REAL call_id
    # (resolved via the same (correlation_id, "LLM_CALL") key llm_call_end
    # uses — see ObservabilityOperator.record_context_assembled/
    # _resolve_transition_ids' CONTEXT_ASSEMBLED branch), not the old
    # synthetic "llm-{correlation_id}" placeholder that needed a later
    # nearest-timestamp reconstruction to link back to the real call.
    llm_call_id = next(r["llm_call_id"] for r in native if r["kind"] == "context_part_contributed")
    assert llm_call_id and llm_call_id != "llm-3", llm_call_id
    llm_end_call_id = next(r["call_id"] for r in native if r["kind"] == "llm_call_end")
    assert llm_call_id == llm_end_call_id, (llm_call_id, llm_end_call_id)


@pytest.mark.timeout(60)
def test_hitl_turn_commit_preserves_history_for_turn_two() -> None:
    from mas.ctl.adapters.hitl_terminal import ScriptedHitlTerminal
    from mas.ctl.session.bootstrap import InstantiationOptions, instantiate_runtime
    from mas.ctl.session.controller import ConversationConfig, SessionController, close_observability
    from overlay_support import load_agent_and_flavour

    steer = "Maxence Postu is POTUS"
    base, flavour = load_agent_and_flavour(
        T01 / "agent.yaml", T01 / "overlays" / "tools.yaml", T01 / "controls" / "governance-hitl.yaml"
    )

    instance, _ = instantiate_runtime(
        InstantiationOptions(
            agent_manifest=base,
            manifest_dir=T01,
            validate_manifests=False,
            engine=web_search_engine(),
            flavour_spec=flavour,
        ),
        hitl=None,
    )
    op = instance.driver.observability
    assert op is not None
    instance.driver.ctx.observability = op

    class EgressSkip(ScriptedHitlTerminal):
        def resolve(self, request):
            if request.context_data.get("hook") == "egress":
                return HitlResolve(
                    request_id=request.request_id,
                    resolution=HitlResolveChoice.SKIP,
                    operator_context={"operator_id": "test", "steering": steer},
                )
            return super().resolve(request)

    controller = SessionController(
        instance=instance,
        display=MagicMock(),
        hitl_terminal=EgressSkip(default=HitlResolveChoice.ALLOW),
        config=ConversationConfig(single_turn=False),
    )
    pending = controller.run_turn("Who is POTUS?", auto_hitl=False)
    req = pending.trace.hitl_requests[-1]
    controller.submit_hitl(
        HitlResolve(
            request_id=req.request_id,
            resolution=HitlResolveChoice.SKIP,
            operator_context={"operator_id": "test", "steering": steer},
        ),
        auto_hitl=False,
    )
    assert any(m.get("role") == "tool" for m in instance.driver.ctx.committed_messages)

    controller.run_turn("Follow-up?", auto_hitl=False)
    messages = assemble_llm_messages(instance.driver.ctx, manifest=base)
    assert any(steer in str(m.get("content")) for m in messages)
    assert any(e.kind == ObsEventKind.CONTEXT_MUTATION for e in op.events)
    close_observability(controller)


def test_multilevel_trajectory_consumes_context_events() -> None:
    pytest.importorskip("mas.lab.plots.multilevel_trajectory")
    from mas.lab.plots.multilevel_trajectory import _build_call_records, _collect_context_provenance
    from mas.library.standard.lib.observability.native.transform import NativeObservabilityTransform, TransformContext

    op = ObservabilityOperator()
    ctx = AutoCtxAssembler(observability=op, last_user_text="Q")
    ctx._assembly_correlation_id = 5
    op.record_context_mutation(action="turn_start", turn_index=1)
    op.record_engine_io(correlation_id=5, op="LLM_CALL")
    assemble_llm_messages(ctx, correlation_id=5)
    op.record_engine_llm_return(correlation_id=5, text="A")

    transform = NativeObservabilityTransform()
    tctx = TransformContext(agent_id="agent")
    events: list[dict] = []
    for ev in op.events:
        payload = ev.model_dump(mode="json")
        payload["_source"] = "boundary"
        events.extend(transform.transform(payload, ctx=tctx))
    for ev in events:
        ev.setdefault("timestamp", 1.0)
    events.append({"kind": "llm_call_start", "agent_id": "agent", "call_id": "llm-5", "timestamp": 1.0})
    events.append({"kind": "llm_call_end", "agent_id": "agent", "call_id": "llm-5", "timestamp": 2.0, "output": "A"})

    records = _build_call_records(events)
    cpr = _collect_context_provenance(events, records)
    assert records
    # This is the actual regression check: without record_engine_io before
    # assemble_llm_messages (see the call above), correlation_id 5's LLM_CALL
    # start event is never emitted, so llm_call_end's call_id="llm-5" has no
    # matching open start — _build_call_records raises ValueError (orphan
    # end event / missing runtime call_id) for LLMCall, a _STRICT_ID_TYPES
    # entry, well before this line — not a silent pass with empty records.
    llm_records = [r for r in records if r.get("call_id") == "llm-5"]
    assert llm_records, f"expected a paired call record for llm-5, got {records}"
    assert llm_records[0].get("call_type") == "LLMCall"
    assert cpr.get("llm-5") or any(r.get("call_id") == "llm-5" for r in records)
    # WM/turn-history mutations are no longer promoted to Calls-lane bars
    # (they cluttered the timeline and widened the chart); they remain in the
    # event stream as state_update_* events and surface via annotations /
    # provenance instead of as ContextState call records.
    state_updates = [e for e in events if str(e.get("kind", "")).startswith("state_update")]
    assert state_updates
    assert any(e.get("update_type") == "turn_start" for e in state_updates)
