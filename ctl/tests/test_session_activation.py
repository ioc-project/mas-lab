#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Session bindings follow the turn executing on a shared runtime."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from mas.ctl.adapters.checkpoint import InMemoryCheckpointStore
from mas.ctl.session import exposure
from mas.ctl.session.controller import SessionController
from mas.ctl.session.manager import SessionManager
from mas.runtime.boundary.context.working_memory_registry import WorkingMemorySnapshot
from mas.runtime.boundary.control.contract import ControlDenied, SessionPaused
from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.engine.simulated import SimulatedEngine
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.session import SessionStatus


class _Runtime:
    def __init__(self) -> None:
        self.instance = RuntimeInstance.from_parts(engine=SimulatedEngine(llm_next_step=lambda _: "STOP"))
        self.driver = self.instance.driver
        self.kernel = self.instance.kernel
        self.calls: list[tuple[str, str]] = []
        self.on_turn: Any = None

    def run_user_text(self, text: str, *, session_id: str, **kwargs: Any) -> Any:
        ctx = self.driver.ctx
        assert ctx.session_id == session_id
        assert ctx.managed_session.session_id == session_id
        assert ctx.managed_session.controller.session_id == session_id
        assert ctx.control.capability.session_ids == frozenset({session_id})
        self.calls.append((session_id, text))
        if self.on_turn is not None:
            self.on_turn(text, session_id)
        return self.instance.run_user_text(text, session_id=session_id, **kwargs)

    def snapshot(self) -> dict[str, Any]:
        return self.instance.snapshot()


@pytest.fixture
def managed_runtime():
    runtime = _Runtime()
    manager = SessionManager(checkpoint_store=InMemoryCheckpointStore())
    for session_id in ("A", "B"):
        controller = SessionController(instance=runtime, display=MagicMock(), agent_id="agent")
        manager.create(
            runtime,
            controller,
            {"metadata": {"name": "agent"}, "spec": {"description": session_id}},
            session_id=session_id,
        )
    return runtime, manager


def test_get_does_not_activate_and_unknown_activation_preserves_bindings(managed_runtime) -> None:
    runtime, manager = managed_runtime
    session = manager.get("A")
    ctx = runtime.driver.ctx
    assert ctx.managed_session is manager.get("B")
    control = ctx.control
    with pytest.raises(KeyError, match="unknown session 'missing'"):
        manager.activate("missing")
    assert set(manager.sessions) == {"A", "B"}
    assert ctx.managed_session is manager.get("B")
    assert ctx.control is control
    assert session.controller.session_id == "A"


@pytest.mark.parametrize("status", [SessionStatus.ACTIVE, SessionStatus.PAUSED, SessionStatus.TERMINATED])
def test_activation_preserves_session_and_live_memory(managed_runtime, status) -> None:
    runtime, manager = managed_runtime
    session = manager.get("A")
    session.status = status
    session.pause_reason = "kept"
    session.controller.restore_turn(4)
    snapshot = WorkingMemorySnapshot(turn_history=[("earlier", "answer")])
    session.working_memory.put("A", "agent", snapshot)
    runtime.driver.ctx.committed_messages = [{"role": "user", "content": "live context"}]
    sandbox = object()
    session.execute_sandbox = sandbox

    for _ in range(2):
        assert manager.activate("A") is session

    ctx = runtime.driver.ctx
    assert ctx.managed_session is session
    assert ctx.session_id == "A"
    assert ctx.control.capability.session_ids == frozenset({"A"})
    assert ctx.current_spec == {"description": "A"}
    assert ctx.execute_sandbox is sandbox
    assert session.controller.managed_session is session
    assert session.controller.working_memory_registry is session.working_memory
    assert session.controller.checkpoint_store is manager.checkpoint_store
    assert session.controller.turn == 4
    assert session.status is status
    assert session.pause_reason == "kept"
    assert session.spec_revision == 0
    assert session.working_memory.get("A", "agent") is snapshot
    assert ctx.committed_messages == [{"role": "user", "content": "live context"}]
    assert set(manager.sessions) == {"A", "B"}


def test_activation_rebinds_governance_and_checkpoint_ownership(managed_runtime) -> None:
    runtime, manager = managed_runtime
    first, second = manager.get("A"), manager.get("B")
    debug = SimpleNamespace(bind_session=MagicMock())
    runtime.kernel.config = KernelConfig(egress_governance_plugin=debug)
    manager.activate("A")

    snapshot = runtime.kernel.config.on_decision_snapshot(hook="authorize", decision="ALLOW")
    assert snapshot.ref.session_id == "A"
    assert manager.snapshot_tree.live("A").snapshot_id == snapshot.ref.snapshot_id
    assert manager.snapshot_tree.live("B") is None
    assert debug.bind_session.call_args.args == (first,)
    assert debug.bind_session.call_args.kwargs["control"].capability.session_ids == frozenset({"A"})

    path = first.checkpoint(first.controller.checkpoint_store, label="active")
    assert manager.checkpoint_store.load_payload(path)["lineage"]["session_id"] == "A"
    assert second.checkpoint_history == []


def test_served_turn_reactivates_session_and_scopes_control_to_current_caller() -> None:
    runtime = _Runtime()
    handler = exposure.make_runtime_handler(runtime)
    handler("first", session_id="A")
    first = handler.manager.get("A")
    controller = first.controller
    handler("second", session_id="B")

    def steer(text: str, session_id: str) -> None:
        control = runtime.driver.ctx.control
        control.steer(session_id, text="only A", mode="after")
        with pytest.raises(ControlDenied):
            control.steer("B", text="wrong caller", mode="after")

    runtime.on_turn = steer
    handler("follow-up", session_id="A")
    assert runtime.calls == [("A", "first"), ("B", "second"), ("A", "follow-up")]
    assert handler.manager.get("A") is first
    assert first.controller is controller
    assert [item.text for item in handler.manager.turn_queue.peek("A")] == ["only A"]
    assert handler.manager.turn_queue.peek("B") == []


def test_existing_session_is_not_readvertised_or_resubscribed(tmp_path) -> None:
    runtime = _Runtime()
    handler = exposure.make_runtime_handler(runtime, control_dir=tmp_path)
    try:
        handler("first", session_id="A")
        subscriptions = list(runtime.driver.exchange_plugins)
        with pytest.MonkeyPatch.context() as patch:
            advertise = MagicMock()
            patch.setattr(handler.host, "advertise", advertise)
            handler("second", session_id="A")
            handler("third", session_id="A")
        advertise.assert_not_called()
        assert len(handler.manager.sessions) == 1
        assert runtime.driver.exchange_plugins == subscriptions
    finally:
        handler.host.close()


def test_paused_session_rejection_and_failure_release_execution() -> None:
    runtime = _Runtime()
    handler = exposure.make_runtime_handler(runtime)
    handler("first", session_id="A")
    handler.manager.get("A").status = SessionStatus.PAUSED
    with pytest.raises(SessionPaused):
        handler("paused", session_id="A")
    assert runtime.calls == [("A", "first")]

    def fail(text: str, session_id: str) -> None:
        raise ValueError("turn failed")

    runtime.on_turn = fail
    with pytest.raises(ValueError, match="turn failed"):
        handler("failure", session_id="B")
    runtime.on_turn = None
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(handler, "recovered", session_id="C").result(timeout=10)["context_id"] == "C"


def test_shared_runtime_serializes_creation_and_activation_but_other_runtimes_progress(monkeypatch) -> None:
    waiting, entered, release = threading.Event(), threading.Event(), threading.Event()

    class ObservedLock:
        def __init__(self) -> None:
            self.lock = threading.RLock()

        def __enter__(self) -> None:
            if not self.lock.acquire(blocking=False):
                waiting.set()
                self.lock.acquire()

        def __exit__(self, *_: Any) -> None:
            self.lock.release()

    monkeypatch.setattr(exposure, "threading", SimpleNamespace(RLock=ObservedLock))
    runtime, other = _Runtime(), _Runtime()
    handler, independent = exposure.make_runtime_handler(runtime), exposure.make_runtime_handler(other)

    def hold(text: str, session_id: str) -> None:
        if session_id == "A":
            entered.set()
            assert release.wait(timeout=10)
            assert runtime.driver.ctx.managed_session.session_id == "A"

    runtime.on_turn = hold
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(handler, "first", session_id="A")
        try:
            assert entered.wait(timeout=10)
            second = pool.submit(handler, "second", session_id="B")
            assert waiting.wait(timeout=10)
            assert set(handler.manager.sessions) == {"A"}
            assert runtime.driver.ctx.managed_session.session_id == "A"
            assert pool.submit(independent, "independent", session_id="C").result(timeout=10)["context_id"] == "C"
        finally:
            release.set()
        assert first.result(timeout=10)["context_id"] == "A"
        assert second.result(timeout=10)["context_id"] == "B"
    assert runtime.calls == [("A", "first"), ("B", "second")]
