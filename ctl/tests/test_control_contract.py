#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""ControlContract: pause is real, queue is FIFO, tree walk is traced."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from mas.ctl.session.controller import SessionController
from mas.ctl.session.manager import SessionManager
from mas.ctl.session.turn_queue import TurnInputQueue
from mas.runtime.boundary.control.contract import (
    ControlCapability,
    ControlDenied,
    SessionPaused,
)
from mas.runtime.driver.mocks import AutoCtxAssembler
from mas.runtime.session import Session, SessionStatus
from mas.runtime.session.snapshot import SnapshotRef, SnapshotTree


def _session(manager: SessionManager, session_id: str = "s1") -> Session:
    ctx = AutoCtxAssembler()
    instance = SimpleNamespace(
        snapshot=lambda: {"q": {}, "run": {}},
        load_checkpoint=lambda data: None,
        pause=lambda **kw: None,
        resume=lambda: None,
        driver=SimpleNamespace(ctx=ctx),
        feed=lambda event: SimpleNamespace(client_responses=[], hitl_requests=[], boundary_errors=[]),
    )

    class _Display:
        def on_system(self, *_a, **_k):
            return None

        def on_user(self, *_a, **_k):
            return None

    controller = SessionController(instance=instance, display=_Display())
    return manager.create(instance, controller, {"name": "agent", "spec": {}}, session_id=session_id)


def test_pause_blocks_the_next_user_turn() -> None:
    manager = SessionManager()
    session = _session(manager)
    control = manager.control(capability=ControlCapability(actor="admin", surface="admin"))
    control.pause("s1", reason="breakpoint")
    assert session.status is SessionStatus.PAUSED
    with pytest.raises(SessionPaused, match="paused"):
        session.controller.run_turn("hello")
    control.resume("s1")
    # no pause error — instance.feed is not a full driver, so we only care pause lifted
    session.controller._reject_if_paused()
    assert session.status is SessionStatus.ACTIVE
    kinds = [e.kind for e in manager.control_events]
    assert kinds == ["session_paused", "session_resumed"]
    assert manager.control_events[0].surface == "admin"


def test_queue_fifo_reorder_and_cancel() -> None:
    queue = TurnInputQueue()
    first = queue.enqueue("s1", "one", source="a")
    second = queue.enqueue("s1", "two", source="a")
    third = queue.enqueue("s1", "three", source="a")
    queue.reorder("s1", [third, first, second])
    queue.cancel("s1", first)
    assert [item.text for item in queue.peek("s1")] == ["three", "two"]
    assert queue.pop("s1").text == "three"
    assert queue.pop("s1").text == "two"
    assert queue.pop("s1") is None
    queue.enqueue("s1", "x", source="a")
    queue.clear_session("s1")
    assert queue.peek("s1") == []


def test_list_and_navigate_tree_is_traced_even_when_denied() -> None:
    tree = SnapshotTree()
    root = SnapshotRef.from_state(
        session_id="s1",
        parent_snapshot_id=None,
        turn=0,
        kernel={"a": 1},
        working_memory=[],
    )
    child = SnapshotRef.from_state(
        session_id="s1",
        parent_snapshot_id=root.snapshot_id,
        turn=1,
        kernel={"a": 2},
        working_memory=[],
    )
    tree.record(root)
    tree.record(child)
    manager = SessionManager(snapshot_tree=tree)
    _session(manager)
    control = manager.control(
        capability=ControlCapability(actor="plugin", surface="plugin"),
        deny_navigate=lambda to: to == child.snapshot_id,
    )
    nodes = control.list_checkpoints("s1")
    assert {n.snapshot_id for n in nodes} == {root.snapshot_id, child.snapshot_id}
    children = [n.snapshot_id for n in tree.children(root.snapshot_id)]
    assert children == [child.snapshot_id]
    with pytest.raises(ControlDenied):
        control.navigate("s1", to=child.snapshot_id, reason="secret")
    denied = [e for e in manager.control_events if e.kind == "checkpoint_navigated"]
    assert denied and denied[0].denied is True
    assert tree.cursor("s1").snapshot_id == root.snapshot_id
    control.deny_navigate = None
    moved = control.navigate("s1", to=child.snapshot_id, reason="inspect")
    assert moved.snapshot_id == child.snapshot_id
    assert tree.cursor("s1").snapshot_id == child.snapshot_id
    assert tree.live("s1").snapshot_id == child.snapshot_id  # last record() set live to child
    view = control.inspect("s1")
    assert view.cursor_snapshot_id == child.snapshot_id


def test_capability_token_scopes_methods_and_sessions() -> None:
    manager = SessionManager()
    _session(manager, "s1")
    observer = manager.control(
        capability=ControlCapability(
            actor="observer",
            surface="llm",
            session_ids=frozenset({"s1"}),
            methods=frozenset({"inspect", "list_checkpoints"}),
        )
    )
    observer.inspect("s1")
    with pytest.raises(ControlDenied, match="capability"):
        observer.pause("s1", reason="no")
    with pytest.raises(ControlDenied):
        manager.control(
            capability=ControlCapability(actor="other", surface="admin", session_ids=frozenset({"other"}))
        ).inspect("s1")


def test_inspect_context_returns_numeric_snapshot_and_cache_rate() -> None:
    from mas.runtime.boundary.obs.operator import ObservabilityOperator

    manager = SessionManager()
    session = _session(manager)
    driver = session.instance.driver
    driver.observability = ObservabilityOperator()
    driver.ctx.last_context_usage = {
        "messages": [{"role": "system", "content": "NEVER_EXPOSE_THIS_PROMPT"}],
        "captured_at": "2026-10-06T12:00:00+00:00",
        "model": "vertex_ai/gemini-2.5-flash",
        "context_window": 1048576,
        "estimated_prompt_tokens": 300,
        "completion_reserve": 12000,
        "estimated_remaining_tokens": 1011276,
        "fill_ratio": 300 / 1048576,
        "token_breakdown": {"context/system": 200, "conversation/user": 100},
        "context_parts": [
            {"source": "skills", "section_id": "skills/active", "tokens": 80, "pinned": True}
        ],
    }
    pricing = {
        "input_per_million_tokens": 1.0,
        "output_per_million_tokens": 10.0,
        "cached_input_per_million_tokens": 0.5,
    }
    driver.observability.record_engine_io_return(
        correlation_id=1,
        op="LLM_CALL",
        usage={"prompt_tokens": 300, "completion_tokens": 20},
        model="vertex_ai/gemini-2.5-flash",
        pricing=pricing,
        cache_status="hit",
        cache_layer="infra_llm_cache",
    )
    driver.observability.record_engine_io_return(
        correlation_id=2,
        op="LLM_CALL",
        usage={"prompt_tokens": 300, "completion_tokens": 21},
        model="vertex_ai/gemini-2.5-flash",
        pricing=pricing,
        cache_status="miss",
        cache_layer="infra_llm_cache",
    )
    driver.observability.record_cache_lookup(
        correlation_id=3,
        cache_layer="infra_llm_cache",
        cache_status="miss",
    )

    view = manager.control(capability=ControlCapability(actor="debugger", surface="admin")).inspect_context("s1")
    assert view.available is True
    assert view.context_window == 1048576
    assert view.estimated_prompt_tokens == 300
    assert view.cache_hits == 1
    assert view.cache_misses == 2
    assert view.cache_hit_rate == 1 / 3
    assert view.latest_provider_usage["completion_tokens"] == 21
    assert view.provider_usage_source == "provider"
    assert view.context_parts[0]["pinned"] is True
    assert view.estimated_cost_usd == pytest.approx((300 * 1.0 + 21 * 10.0) / 1_000_000)
    assert view.cost_status == "catalog_estimate"
    assert "NEVER_EXPOSE_THIS_PROMPT" not in repr(view)
    assert not hasattr(view, "messages")


def test_inspect_archive_returns_covering_tree() -> None:
    manager = SessionManager()
    session = _session(manager)
    ctx = session.instance.driver.ctx
    ctx.note_user_input("hello")
    ctx.note_agent_response("world")
    dump = manager.control(
        capability=ControlCapability(actor="debugger", surface="admin")
    ).inspect_archive("s1")
    assert dump["covers_archive"] is True
    assert dump["covering_ids"]
    assert dump["leaves"]
    assert dump["view_id"] == ctx.conversation_chunks.view_id


def test_checkpoint_does_not_unpause_a_paused_session() -> None:
    manager = SessionManager()
    session = _session(manager)
    session.pause(reason="hold")

    class _Store:
        def save(self, snapshot, *, label=""):
            from pathlib import Path

            return Path("x.checkpoint.json")

        def retain(self, *args, **kwargs):
            return None

    session.checkpoint(_Store(), label="held")
    assert session.status is SessionStatus.PAUSED


def test_queue_revision_cas_and_action_change() -> None:
    from mas.runtime.boundary.control.contract import QueueConflict, QueueItemGone

    queue = TurnInputQueue()
    first = queue.enqueue("s1", "one", source="a")
    second = queue.enqueue("s1", "two", source="a")
    view = queue.inspect("s1")
    assert view.revision == 2
    queue.set_action("s1", first, action="steer", revision=view.revision)
    with pytest.raises(QueueConflict):
        queue.reorder("s1", [second, first], revision=view.revision)
    view = queue.inspect("s1")
    assert view.items[0].action == "steer"
    queue.cancel("s1", first, revision=view.revision)
    with pytest.raises(QueueItemGone):
        queue.cancel("s1", first)


def test_steer_preempts_an_inflight_llm() -> None:
    from mas.runtime.engine import inflight_llm

    manager = SessionManager()
    session = _session(manager)
    control = manager.control(capability=ControlCapability(actor="admin", surface="admin"))
    session.controller.inflight = True
    inflight_llm._tasks["s1"] = type("T", (), {"done": lambda self: False, "cancel": lambda self: None})()
    try:
        control.steer("s1", text="use Lyon")
        assert inflight_llm.peek_preempt("s1") == "use Lyon"
        control.steer("s1", text="later", mode="after")
        queued = control.inspect_queue("s1")
        assert queued.items[0].action == "turn"
        assert queued.items[0].text == "later"
    finally:
        inflight_llm._tasks.pop("s1", None)
        inflight_llm._preempts.pop("s1", None)


def test_steer_replace_discards_partials() -> None:
    from mas.runtime.engine import inflight_llm

    manager = SessionManager()
    session = _session(manager)
    control = manager.control(capability=ControlCapability(actor="admin", surface="admin"))
    session.controller.inflight = True
    inflight_llm._tasks["s1"] = type("T", (), {"done": lambda self: False, "cancel": lambda self: None})()
    inflight_llm.append_partial("s1", "Hello ")
    try:
        control.steer("s1", text="start over", mode="replace")
        assert inflight_llm.peek_replace("s1") == "start over"
        assert inflight_llm.peek_preempt("s1") is None
        assert inflight_llm.take_partial("s1") == ""
    finally:
        inflight_llm._tasks.pop("s1", None)
        inflight_llm._replaces.pop("s1", None)
        inflight_llm._partials.pop("s1", None)


def test_steer_while_turn_inflight_without_llm_queues_to_front() -> None:
    manager = SessionManager()
    session = _session(manager)
    control = manager.control(capability=ControlCapability(actor="admin", surface="admin"))
    session.controller.inflight = True
    control.enqueue_input("s1", text="later", source="peer")
    control.steer("s1", text="now")
    texts = [item.text for item in control.inspect_queue("s1").items]
    assert texts[0].startswith("/steer now")
    assert texts[1] == "later"


def test_enqueue_at_head_and_index() -> None:
    queue = TurnInputQueue()
    queue.enqueue("s1", "one", source="a")
    queue.enqueue("s1", "two", source="a")
    queue.enqueue("s1", "head", source="a", at="head")
    queue.enqueue("s1", "mid", source="a", at=1)
    queue.enqueue("s1", "tail", source="a", at="tail")
    assert [item.text for item in queue.peek("s1")] == ["head", "mid", "one", "two", "tail"]
    with pytest.raises(ValueError, match="out of range"):
        queue.enqueue("s1", "bad", source="a", at=9)
    with pytest.raises(ValueError, match="out of range"):
        queue.enqueue("s1", "bad", source="a", at=-1)


def test_steer_after_queues_to_front() -> None:
    manager = SessionManager()
    _session(manager)
    control = manager.control(capability=ControlCapability(actor="admin", surface="admin"))
    control.enqueue_input("s1", text="later", source="peer")
    control.steer("s1", text="now", mode="after")
    texts = [item.text for item in control.inspect_queue("s1").items]
    assert texts[0] == "now"
    assert texts[1] == "later"
    with pytest.raises(ValueError, match="mode='after'"):
        control.steer("s1", text="no", mode="preempt", at=1)


def test_send_message_queues_and_never_amends() -> None:
    from mas.runtime.engine import inflight_llm

    manager = SessionManager()
    session = _session(manager)
    control = manager.control(capability=ControlCapability(actor="admin", surface="admin"))
    session.controller.inflight = True
    inflight_llm._tasks["s1"] = type("T", (), {"done": lambda self: False, "cancel": lambda self: None})()
    try:
        input_id = control.send_message("s1", text="also consider Lyon")
        assert input_id
        queued = control.inspect_queue("s1")
        assert queued.items[0].text == "also consider Lyon"
        assert queued.items[0].action == "turn"
        assert inflight_llm.peek_amend("s1") is None
        events = [e for e in manager.control_events if e.method == "send_message"]
        assert events and events[0].kind == "input_enqueued"
    finally:
        inflight_llm._tasks.pop("s1", None)
        inflight_llm._preempts.pop("s1", None)


def test_llm_tools_cannot_send_message() -> None:
    manager = SessionManager()
    session = _session(manager)
    llm = session.instance.driver.ctx.control
    with pytest.raises(ControlDenied, match="send_message"):
        llm.send_message("s1", text="no")
