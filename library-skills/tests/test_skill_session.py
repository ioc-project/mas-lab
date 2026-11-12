#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for SkillSessionState — activation deduplication + turn tracking."""

from __future__ import annotations

from mas.library.skills.lib.registry import SkillRecord, SkillRegistry
from mas.library.skills.lib.session import ActivatedSkill, SkillSessionState
from mas.library.skills.plugins.sk_tools import SkillToolsPlugin

# ---------------------------------------------------------------------------
# SkillSessionState unit tests
# ---------------------------------------------------------------------------


def test_initial_state_is_empty():
    state = SkillSessionState()
    assert not state
    assert len(state) == 0
    assert state.activated_names() == []


def test_mark_activated():
    state = SkillSessionState()
    rec = state.mark_activated("code-review", turn=3)
    assert isinstance(rec, ActivatedSkill)
    assert rec.name == "code-review"
    assert rec.turn == 3
    assert rec.notices == 0


def test_is_activated_true_after_mark():
    state = SkillSessionState()
    assert not state.is_activated("code-review")
    state.mark_activated("code-review")
    assert state.is_activated("code-review")


def test_is_activated_false_for_unknown():
    state = SkillSessionState()
    state.mark_activated("skill-a")
    assert not state.is_activated("skill-b")


def test_mark_activated_idempotent():
    state = SkillSessionState()
    r1 = state.mark_activated("code-review", turn=1)
    r2 = state.mark_activated("code-review", turn=5)  # second call
    assert r1 is r2  # same object returned
    assert r2.turn == 1  # turn not updated on repeat


def test_mark_deactivated_removes_skill():
    state = SkillSessionState()
    state.mark_activated("code-review")
    assert state.mark_deactivated("code-review") is True
    assert not state.is_activated("code-review")
    assert state.activated_names() == []


def test_mark_deactivated_unknown_is_false():
    state = SkillSessionState()
    assert state.mark_deactivated("ghost") is False


def test_note_reactivation_attempt():
    state = SkillSessionState()
    state.mark_activated("code-review")
    assert state.get("code-review").notices == 0
    state.note_reactivation_attempt("code-review")
    assert state.get("code-review").notices == 1
    state.note_reactivation_attempt("code-review")
    assert state.get("code-review").notices == 2


def test_note_reactivation_noop_for_unknown():
    state = SkillSessionState()
    state.note_reactivation_attempt("ghost")  # should not raise


def test_activated_names():
    state = SkillSessionState()
    state.mark_activated("a")
    state.mark_activated("b")
    names = state.activated_names()
    assert "a" in names
    assert "b" in names


def test_repr():
    state = SkillSessionState()
    state.mark_activated("x")
    assert "x" in repr(state)


# ---------------------------------------------------------------------------
# SkillToolsPlugin — deduplication via session state
# ---------------------------------------------------------------------------


def _make_registry_and_ctx(tmp_path, skill_name="code-review"):

    skill_dir = tmp_path / skill_name
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        f"---\nname: {skill_name}\ndescription: Reviews code.\n---\n# Body\n",
        encoding="utf-8",
    )
    reg = SkillRegistry()
    reg.register(SkillRecord(name=skill_name, description="Reviews code.", path=skill_md))

    class _Ctx:
        pass

    ctx = _Ctx()
    ctx.skill_registry = reg
    ctx.skill_session_state = SkillSessionState()
    ctx.turn_index = 2
    return reg, ctx


def test_first_activation_loads_body(tmp_path):
    _, ctx = _make_registry_and_ctx(tmp_path)
    plugin = SkillToolsPlugin()
    result = plugin.on_execute_tool("activate_skill", {"name": "code-review"}, ctx=ctx)
    assert "error" not in result
    assert "content" in result
    assert "# Body" in result["content"]


def test_first_activation_marks_session(tmp_path):
    _, ctx = _make_registry_and_ctx(tmp_path)
    plugin = SkillToolsPlugin()
    plugin.on_execute_tool("activate_skill", {"name": "code-review"}, ctx=ctx)
    assert ctx.skill_session_state.is_activated("code-review")
    rec = ctx.skill_session_state.get("code-review")
    assert rec.turn == 2  # from ctx.turn_index


def test_second_activation_returns_notice(tmp_path):
    _, ctx = _make_registry_and_ctx(tmp_path)
    plugin = SkillToolsPlugin()
    # First call — loads body
    r1 = plugin.on_execute_tool("activate_skill", {"name": "code-review"}, ctx=ctx)
    assert "content" in r1
    # Second call — deduplication
    r2 = plugin.on_execute_tool("activate_skill", {"name": "code-review"}, ctx=ctx)
    assert "notice" in r2
    assert r2.get("already_activated") is True
    assert "already in context" in r2["notice"]


def test_second_activation_increments_notices(tmp_path):
    _, ctx = _make_registry_and_ctx(tmp_path)
    plugin = SkillToolsPlugin()
    plugin.on_execute_tool("activate_skill", {"name": "code-review"}, ctx=ctx)
    plugin.on_execute_tool("activate_skill", {"name": "code-review"}, ctx=ctx)
    plugin.on_execute_tool("activate_skill", {"name": "code-review"}, ctx=ctx)
    assert ctx.skill_session_state.get("code-review").notices == 2


def test_no_dedup_without_session_state(tmp_path):
    """If ctx has no skill_session_state, activate_skill loads body every time."""
    reg, ctx = _make_registry_and_ctx(tmp_path)
    del ctx.skill_session_state  # remove session state
    plugin = SkillToolsPlugin()
    r1 = plugin.on_execute_tool("activate_skill", {"name": "code-review"}, ctx=ctx)
    assert "content" in r1
    r2 = plugin.on_execute_tool("activate_skill", {"name": "code-review"}, ctx=ctx)
    assert "content" in r2  # no dedup — loads again


def test_different_skills_both_activate(tmp_path):
    _, ctx = _make_registry_and_ctx(tmp_path, "code-review")
    # Add second skill to registry
    skill2_dir = tmp_path / "triage"
    skill2_dir.mkdir()
    skill2_md = skill2_dir / "SKILL.md"
    skill2_md.write_text(
        "---\nname: triage\ndescription: Triages issues.\n---\n# Triage body\n",
        encoding="utf-8",
    )
    ctx.skill_registry.register(SkillRecord(name="triage", description="Triages issues.", path=skill2_md))

    plugin = SkillToolsPlugin()
    r1 = plugin.on_execute_tool("activate_skill", {"name": "code-review"}, ctx=ctx)
    r2 = plugin.on_execute_tool("activate_skill", {"name": "triage"}, ctx=ctx)
    assert "content" in r1
    assert "content" in r2
    # Both marked in session
    assert ctx.skill_session_state.is_activated("code-review")
    assert ctx.skill_session_state.is_activated("triage")


def test_deactivate_unpins_and_allows_reload(tmp_path):
    _, ctx = _make_registry_and_ctx(tmp_path)
    ctx.skill_allow_unload = True
    plugin = SkillToolsPlugin()
    plugin.on_execute_tool("activate_skill", {"name": "code-review"}, ctx=ctx)

    class _Pin:
        def __init__(self):
            self.removed = []

        def add_activated(self, name, body):
            pass

        def remove_activated(self, name):
            self.removed.append(name)

    pin = _Pin()
    ctx.activated_skills_plugin = pin
    result = plugin.on_execute_tool("activate_skill", {"name": "code-review", "unload": True}, ctx=ctx)
    assert result["deactivated"] is True
    assert "no longer in force" in result["notice"]
    assert not ctx.skill_session_state.is_activated("code-review")
    assert pin.removed == ["code-review"]
    reload = plugin.on_execute_tool("activate_skill", {"name": "code-review"}, ctx=ctx)
    assert "content" in reload
    assert ctx.skill_session_state.is_activated("code-review")


def test_unload_string_true(tmp_path):
    _, ctx = _make_registry_and_ctx(tmp_path)
    ctx.skill_allow_unload = True
    plugin = SkillToolsPlugin()
    plugin.on_execute_tool("activate_skill", {"name": "code-review"}, ctx=ctx)
    result = plugin.on_execute_tool("activate_skill", {"name": "code-review", "unload": "true"}, ctx=ctx)
    assert result["deactivated"] is True


def test_unload_param_not_advertised_by_default(tmp_path):
    _, ctx = _make_registry_and_ctx(tmp_path)
    plugin = SkillToolsPlugin()
    activate = next(t for t in plugin.on_collect_tools(ctx=ctx) if t["name"] == "activate_skill")
    assert "unload" not in activate["parameters"]["properties"]
    names = {t["name"] for t in plugin.on_collect_tools(ctx=ctx)}
    assert names == {"activate_skill", "list_skill_files", "read_skill_file"}


def test_unload_unknown_skill_names_the_miss(tmp_path):
    _, ctx = _make_registry_and_ctx(tmp_path)
    ctx.skill_allow_unload = True
    plugin = SkillToolsPlugin()
    result = plugin.on_execute_tool("activate_skill", {"name": "does-not-exist", "unload": True}, ctx=ctx)
    assert "not found" in result["error"]
    assert "does-not-exist" in result["error"]


def test_unload_rejected_when_disabled(tmp_path):
    _, ctx = _make_registry_and_ctx(tmp_path)
    ctx.skill_allow_unload = False
    plugin = SkillToolsPlugin()
    plugin.on_execute_tool("activate_skill", {"name": "code-review"}, ctx=ctx)
    result = plugin.on_execute_tool("activate_skill", {"name": "code-review", "unload": True}, ctx=ctx)
    assert result["deactivated"] is False
    assert "disabled" in result["error"]
    assert "Agent Skills spec" in result["error"]
    assert ctx.skill_session_state.is_activated("code-review")
