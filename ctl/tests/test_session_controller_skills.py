#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for SessionController's `/skills` and `/skill <name>` commands --
the agentskills.io Step 4 user-explicit activation path (harness-driven
lookup + injection, no LLM turn involved)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("mas.library.skills", reason="requires the skills library")

from mas.ctl.session.controller import ConversationConfig, SessionController
from mas.library.skills.lib.registry import SkillRecord, SkillRegistry
from mas.library.skills.lib.session import SkillSessionState
from mas.runtime.boundary.context.working_memory_registry import (
    WorkingMemoryConfig,
    reset_working_memory_registry,
)
from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.engine.simulated import SimulatedEngine


class _CapturingDisplay:
    def __init__(self) -> None:
        self.system_messages: list[str] = []

    def on_user(self, text: str, *, turn_id: str = "") -> None:
        return

    def on_agent(self, text: str) -> None:
        return

    def on_turn_error(self, message: str, *, detail: str = "") -> None:
        return

    def on_hitl_request(self, request: object) -> None:
        return

    def on_system(self, message: str) -> None:
        self.system_messages.append(message)

    def on_error(self, message: str) -> None:
        return


def _registry_with_skill(tmp_path: Path, name: str = "my-skill") -> SkillRegistry:
    skill_dir = tmp_path / name
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        f"---\nname: {name}\ndescription: Does something.\n---\n# {name}\n\nBody text.\n",
        encoding="utf-8",
    )
    reg = SkillRegistry()
    reg.register(SkillRecord(name=name, description="Does something.", path=skill_md))
    return reg


def _idle_engine() -> SimulatedEngine:
    """An engine that should never actually be invoked by these tests --
    /skills and /skill are handled entirely by the harness, before any
    LLM_CALL is issued."""

    def next_step(_cid: int) -> str:
        return "STOP"

    engine = SimulatedEngine(llm_next_step=next_step)
    original_invoke = engine.invoke

    def invoke(io):
        if io.op == "LLM_CALL":
            raise AssertionError("/skills and /skill must not trigger an LLM call")
        return original_invoke(io)

    engine.invoke = invoke  # type: ignore[method-assign]
    return engine


def _controller(*, session_id: str, display: _CapturingDisplay) -> SessionController:
    instance = RuntimeInstance.from_parts(engine=_idle_engine())
    instance.working_memory = WorkingMemoryConfig(persistent=True)
    instance.capture_session_baseline()
    return SessionController(
        instance=instance,
        display=display,
        config=ConversationConfig(single_turn=False),
        agent_id="schedule_agent",
        session_id=session_id,
    )


def test_skills_command_lists_no_skills_when_registry_missing() -> None:
    reset_working_memory_registry()
    display = _CapturingDisplay()
    controller = _controller(session_id="session-skills-empty", display=display)

    controller.run_turn("/skills", auto_hitl=False)

    assert display.system_messages == [
        "Skills are not configured for this agent (no SkillCatalogPlugin attached)."
    ]


def test_skills_command_lists_no_skills_when_registry_empty() -> None:
    reset_working_memory_registry()
    display = _CapturingDisplay()
    controller = _controller(session_id="session-skills-registry-empty", display=display)
    controller.instance.driver.ctx.skill_registry = SkillRegistry()

    controller.run_turn("/skills", auto_hitl=False)

    assert display.system_messages == ["No skills registered for this agent."]


def test_skills_command_lists_registered_skills(tmp_path: Path) -> None:
    reset_working_memory_registry()
    display = _CapturingDisplay()
    controller = _controller(session_id="session-skills-list", display=display)
    controller.instance.driver.ctx.skill_registry = _registry_with_skill(tmp_path, "my-skill")

    controller.run_turn("/skills", auto_hitl=False)

    assert len(display.system_messages) == 1
    message = display.system_messages[0]
    assert "my-skill" in message
    assert "Does something." in message
    assert "[activated]" not in message


def test_skill_command_activates_and_injects_content(tmp_path: Path) -> None:
    reset_working_memory_registry()
    display = _CapturingDisplay()
    controller = _controller(session_id="session-skill-activate", display=display)
    ctx = controller.instance.driver.ctx
    ctx.skill_registry = _registry_with_skill(tmp_path, "my-skill")

    controller.run_turn("/skill my-skill", auto_hitl=False)

    assert any("activated" in msg for msg in display.system_messages)
    assert any("Body text" in injected for injected in ctx.injected_context)


def test_skill_command_unknown_name_reports_error() -> None:
    reset_working_memory_registry()
    display = _CapturingDisplay()
    controller = _controller(session_id="session-skill-unknown", display=display)
    controller.instance.driver.ctx.skill_registry = SkillRegistry()

    controller.run_turn("/skill does-not-exist", auto_hitl=False)

    assert any("error" in msg.lower() for msg in display.system_messages)


def test_bare_skill_command_shows_usage_via_dispatch() -> None:
    """`/skill` alone (no name) must be caught by run_turn()'s dispatch and
    show usage -- must NOT fall through to a normal (LLM) user turn.

    Regression test: the dispatch previously only matched
    `stripped.startswith("/skill ")`, which a bare `/skill` (no trailing
    space survives `text.strip()`) never satisfies, silently sending the
    command to the model instead."""
    reset_working_memory_registry()
    display = _CapturingDisplay()
    controller = _controller(session_id="session-skill-bare", display=display)

    controller.run_turn("/skill", auto_hitl=False)

    assert display.system_messages == ["Usage: /skill <name>"]


def test_skill_command_with_trailing_whitespace_only_shows_usage() -> None:
    """`/skill   ` (trailing whitespace, no name) must also show usage."""
    reset_working_memory_registry()
    display = _CapturingDisplay()
    controller = _controller(session_id="session-skill-whitespace", display=display)

    controller.run_turn("/skill   ", auto_hitl=False)

    assert display.system_messages == ["Usage: /skill <name>"]


def test_skill_command_activating_twice_does_not_duplicate_injected_content(
    tmp_path: Path,
) -> None:
    """Double-injection guard (agentskills.io Step 5): a second /skill call
    for an already-activated skill returns a notice instead of re-injecting
    the full body into ctx.injected_context."""
    reset_working_memory_registry()
    display = _CapturingDisplay()
    controller = _controller(session_id="session-skill-dedup", display=display)
    ctx = controller.instance.driver.ctx
    ctx.skill_registry = _registry_with_skill(tmp_path, "my-skill")
    ctx.skill_session_state = SkillSessionState()

    controller.run_turn("/skill my-skill", auto_hitl=False)
    controller.run_turn("/skill my-skill", auto_hitl=False)

    injections = [c for c in ctx.injected_context if "Body text" in c]
    assert len(injections) == 1
    assert any("already in context" in msg.lower() for msg in display.system_messages)
