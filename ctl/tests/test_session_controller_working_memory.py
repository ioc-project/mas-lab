#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""SessionController itself syncs working memory via WorkingMemoryRegistry --
the same mechanism make_workflow_send uses for delegated calls now backs
directly-driven sessions (chat) too. See the harmonization discussed in
docs/design/working-memory-compaction.md: one mechanism, not two."""

from __future__ import annotations

from mas.ctl.session.controller import ConversationConfig, SessionController
from mas.runtime.boundary.context.working_memory_registry import (
    WorkingMemoryConfig,
    WorkingMemoryRegistry,
    get_working_memory_registry,
    reset_working_memory_registry,
)
from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.engine.simulated import SimulatedEngine
from mas.runtime.schema.ingress import EngineIoReturn


class _SilentDisplay:
    def on_user(self, text: str, *, turn_id: str = "") -> None:
        return

    def on_agent(self, text: str) -> None:
        return

    def on_turn_error(self, message: str, *, detail: str = "") -> None:
        return

    def on_hitl_request(self, request: object) -> None:
        return

    def on_system(self, message: str) -> None:
        return

    def on_error(self, message: str) -> None:
        return


def _scripted_text_engine(responses: list[str]) -> SimulatedEngine:
    """A SimulatedEngine that replies with each of `responses` in turn,
    regardless of correlation_id (one LLM_CALL per run_turn -- correlation
    ids increase across controller instances too, so a plain counter keyed
    off correlation_id parity is not reliable across several controllers)."""
    calls = {"n": 0}

    def next_step(_cid: int) -> str:
        return "STOP"

    engine = SimulatedEngine(llm_next_step=next_step)
    original_invoke = engine.ainvoke

    async def ainvoke(io):
        if io.op == "LLM_CALL":
            text = responses[min(calls["n"], len(responses) - 1)]
            calls["n"] += 1
            return EngineIoReturn(
                correlation_id=io.correlation_id,
                response_kind="MODEL_TEXT",
                next_step="STOP",
                text=text,
            )
        return await original_invoke(io)

    engine.ainvoke = ainvoke  # type: ignore[method-assign]
    return engine


def _controller(
    *,
    responses: list[str],
    session_id: str,
    agent_id: str = "schedule_agent",
    persistent: bool = True,
    registry: WorkingMemoryRegistry | None = None,
) -> SessionController:
    instance = RuntimeInstance.from_parts(engine=_scripted_text_engine(responses))
    instance.working_memory = WorkingMemoryConfig(persistent=persistent)
    instance.capture_session_baseline()
    return SessionController(
        instance=instance,
        display=_SilentDisplay(),
        config=ConversationConfig(single_turn=False),
        agent_id=agent_id,
        session_id=session_id,
        working_memory_registry=registry,
    )


def test_second_controller_same_session_and_agent_recovers_history() -> None:
    """The moderator/Foo-then-Bar scenario, but for a directly-driven session
    (not delegation): a brand new SessionController (fresh RuntimeInstance,
    fresh ctx) for the SAME (session_id, agent_id) picks up the first
    controller's history -- proving SessionController itself now syncs via
    the registry, not by relying on the same Python object staying alive."""
    reset_working_memory_registry()
    session_id = "session-chat-xyz"

    first = _controller(responses=["Here is Foo"], session_id=session_id)
    first.run_turn("Please generate Foo", auto_hitl=False)

    second = _controller(responses=["Here is FooBar"], session_id=session_id)
    ctx = second.instance.driver.ctx
    assert ctx.committed_messages == []  # sanity: genuinely fresh ctx

    second.run_turn("Please add Bar", auto_hitl=False)

    contents = [m["content"] for m in ctx.committed_messages]
    assert "Please generate Foo" in contents
    assert "Here is Foo" in contents
    assert "Please add Bar" in contents
    assert "Here is FooBar" in contents


def test_different_agent_id_same_session_does_not_share_history() -> None:
    reset_working_memory_registry()
    session_id = "session-chat-agents"

    a = _controller(responses=["a says hi"], session_id=session_id, agent_id="agent-a")
    a.run_turn("hello a", auto_hitl=False)

    b = _controller(responses=["b says hi"], session_id=session_id, agent_id="agent-b")
    b.run_turn("hello b", auto_hitl=False)

    contents = [m["content"] for m in b.instance.driver.ctx.committed_messages]
    assert "hello a" not in contents
    assert "hello b" in contents


def test_separate_session_registries_do_not_share_history() -> None:
    session_id = "session-isolation"
    first = _controller(
        responses=["private answer"],
        session_id=session_id,
        registry=WorkingMemoryRegistry(),
    )
    second = _controller(
        responses=["independent answer"],
        session_id=session_id,
        registry=WorkingMemoryRegistry(),
    )

    first.run_turn("private question", auto_hitl=False)
    second.run_turn("independent question", auto_hitl=False)

    second_contents = [m["content"] for m in second.instance.driver.ctx.committed_messages]
    assert "private question" not in second_contents
    assert "private answer" not in second_contents
    assert "independent question" in second_contents


def test_persistent_false_starts_fresh_every_turn_even_on_the_same_controller() -> None:
    """A directly-chatted agent can now opt out of remembering across turns
    -- previously the only way to get this was manual /reset before every
    turn. Same instance/ctx object reused across both run_turn calls (one
    long-lived chat process), same as today, but persistent: false means
    each turn starts blank anyway."""
    reset_working_memory_registry()
    session_id = "session-stateless-chat"
    controller = _controller(
        responses=["translated: hola", "translated: adios"],
        session_id=session_id,
        persistent=False,
    )

    controller.run_turn("hola", auto_hitl=False)
    first_turn_committed = list(controller.instance.driver.ctx.committed_messages)
    assert any(m["content"] == "hola" for m in first_turn_committed)

    controller.run_turn("adios", auto_hitl=False)
    contents = [m["content"] for m in controller.instance.driver.ctx.committed_messages]
    assert "hola" not in contents  # cleared before the second turn ran
    assert "adios" in contents
    assert get_working_memory_registry().get(session_id, controller.agent_id) is None


def test_reset_session_drops_the_registry_snapshot_so_a_new_controller_starts_fresh() -> None:
    """Regression: without dropping the registry entry on reset, a NEW
    controller for the same (session_id, agent_id) would restore the
    pre-reset history right back, silently undoing the user's /reset."""
    reset_working_memory_registry()
    session_id = "session-chat-reset"

    first = _controller(responses=["Here is Foo"], session_id=session_id)
    first.run_turn("Please generate Foo", auto_hitl=False)
    assert first.reset_session() is True

    second = _controller(responses=["Hello again"], session_id=session_id)
    second.run_turn("fresh start", auto_hitl=False)

    contents = [m["content"] for m in second.instance.driver.ctx.committed_messages]
    assert "Please generate Foo" not in contents
    assert "fresh start" in contents
