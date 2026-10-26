#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for SkillToolsPlugin — ToolContract skill-access tools."""

from __future__ import annotations

from pathlib import Path

from mas.library.skills.lib.registry import SkillRecord, SkillRegistry
from mas.library.skills.plugins.plugin_skills_native import NativeSkillPlugin
from mas.library.skills.plugins.sk_tools import SkillToolsPlugin


def _registry_with_skill(tmp_path: Path, name: str = "my-skill") -> tuple[SkillRegistry, Path]:
    skill_dir = tmp_path / name
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        f"---\nname: {name}\ndescription: Does something.\n---\n# {name}\n\nBody text.\n",
        encoding="utf-8",
    )
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "rules.md").write_text("# Rules\n\nContent.", encoding="utf-8")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "run.py").write_text("print('hello')", encoding="utf-8")

    reg = SkillRegistry()
    reg.register(SkillRecord(name=name, description="Does something.", path=skill_md))
    return reg, skill_dir


def _registry_with_skills(tmp_path: Path, names: list[str]) -> SkillRegistry:
    reg = SkillRegistry()
    for name in names:
        skill_dir = tmp_path / name
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(
            f"---\nname: {name}\ndescription: Does something.\n---\n# {name}\n\nBody text.\n",
            encoding="utf-8",
        )
        reg.register(SkillRecord(name=name, description="Does something.", path=skill_md))
    return reg


class _FakeSessionState:
    def __init__(self, activated: list[str]):
        self._activated = list(activated)

    def activated_names(self) -> list[str]:
        return list(self._activated)


class _FakeCtx:
    def __init__(
        self,
        registry: SkillRegistry | None = None,
        skill_session_state: _FakeSessionState | None = None,
    ):
        if registry is not None:
            self.skill_registry = registry
        if skill_session_state is not None:
            self.skill_session_state = skill_session_state


# ---------------------------------------------------------------------------
# activate_skill
# ---------------------------------------------------------------------------

def test_activate_skill_returns_body(tmp_path: Path):
    reg, _ = _registry_with_skill(tmp_path)
    plugin = SkillToolsPlugin()
    result = plugin.on_execute_tool("activate_skill", {"name": "my-skill"}, ctx=_FakeCtx(reg))
    assert "content" in result
    assert "Body text" in result["content"]
    assert "<skill_content" in result["content"]
    assert "</skill_content>" in result["content"]
    assert result["chunk"]["pin_policy"] == "never"
    assert result["chunk"]["semantic"] == "instruction"
    assert result["chunk"]["section_id"] == "skills/activated/my-skill"


def test_activate_skill_strips_frontmatter(tmp_path: Path):
    reg, _ = _registry_with_skill(tmp_path)
    plugin = SkillToolsPlugin()
    result = plugin.on_execute_tool("activate_skill", {"name": "my-skill"}, ctx=_FakeCtx(reg))
    assert "---" not in result["content"]
    assert "description:" not in result["content"]


def test_activate_skill_lists_resources(tmp_path: Path):
    reg, _ = _registry_with_skill(tmp_path)
    plugin = SkillToolsPlugin()
    result = plugin.on_execute_tool("activate_skill", {"name": "my-skill"}, ctx=_FakeCtx(reg))
    assert "<skill_resources>" in result["content"]
    assert "references/rules.md" in result["content"]
    assert "scripts/run.py" in result["content"]


def test_activate_skill_unknown_name(tmp_path: Path):
    reg, _ = _registry_with_skill(tmp_path)
    plugin = SkillToolsPlugin()
    result = plugin.on_execute_tool("activate_skill", {"name": "ghost"}, ctx=_FakeCtx(reg))
    assert "error" in result
    assert "ghost" in result["error"]


def test_activate_skill_no_registry():
    plugin = SkillToolsPlugin()
    result = plugin.on_execute_tool("activate_skill", {"name": "x"}, ctx=_FakeCtx(None))
    assert "error" in result


def test_activate_skill_missing_name():
    plugin = SkillToolsPlugin()
    result = plugin.on_execute_tool("activate_skill", {}, ctx=_FakeCtx(None))
    assert "error" in result


def test_activate_skill_non_string_name_rejected():
    """A malformed tool call (e.g. name as a list/int) must be rejected with
    a clear error, not silently coerced via str() into a bogus lookup key."""
    plugin = SkillToolsPlugin()
    result = plugin.on_execute_tool("activate_skill", {"name": ["a", "b"]}, ctx=_FakeCtx(None))
    assert "error" in result
    assert "must be a string" in result["error"]


def test_read_skill_file_non_string_path_rejected(tmp_path: Path):
    reg, _ = _registry_with_skill(tmp_path)
    plugin = SkillToolsPlugin()
    result = plugin.on_execute_tool(
        "read_skill_file", {"skill": "my-skill", "path": 123}, ctx=_FakeCtx(reg)
    )
    assert "error" in result
    assert "must be a string" in result["error"]


# ---------------------------------------------------------------------------
# list_skill_files
# ---------------------------------------------------------------------------

def test_list_skill_files(tmp_path: Path):
    reg, _ = _registry_with_skill(tmp_path)
    plugin = SkillToolsPlugin()
    result = plugin.on_execute_tool("list_skill_files", {"skill": "my-skill"}, ctx=_FakeCtx(reg))
    assert "files" in result
    files = result["files"]
    assert any("references/rules.md" in f for f in files)
    assert any("scripts/run.py" in f for f in files)
    assert not any("SKILL.md" in f for f in files)


def test_list_skill_files_unknown(tmp_path: Path):
    reg, _ = _registry_with_skill(tmp_path)
    plugin = SkillToolsPlugin()
    result = plugin.on_execute_tool("list_skill_files", {"skill": "nope"}, ctx=_FakeCtx(reg))
    assert "error" in result


# ---------------------------------------------------------------------------
# read_skill_file
# ---------------------------------------------------------------------------

def test_read_skill_file_ok(tmp_path: Path):
    reg, _ = _registry_with_skill(tmp_path)
    plugin = SkillToolsPlugin()
    result = plugin.on_execute_tool(
        "read_skill_file", {"skill": "my-skill", "path": "references/rules.md"},
        ctx=_FakeCtx(reg),
    )
    assert "content" in result
    assert "# Rules" in result["content"]


def test_read_skill_file_traversal_blocked(tmp_path: Path):
    reg, _ = _registry_with_skill(tmp_path)
    plugin = SkillToolsPlugin()
    result = plugin.on_execute_tool(
        "read_skill_file", {"skill": "my-skill", "path": "../../etc/passwd"},
        ctx=_FakeCtx(reg),
    )
    assert "error" in result
    assert "escapes" in result["error"]


def test_read_skill_file_not_found(tmp_path: Path):
    reg, _ = _registry_with_skill(tmp_path)
    plugin = SkillToolsPlugin()
    result = plugin.on_execute_tool(
        "read_skill_file", {"skill": "my-skill", "path": "nonexistent.md"},
        ctx=_FakeCtx(reg),
    )
    assert "error" in result


def test_read_skill_file_non_utf8_binary_asset_returns_clean_error(tmp_path: Path):
    """UnicodeDecodeError from read_text(encoding="utf-8") on a binary asset
    must be caught and reported as a clean {"error": ...} -- it is not an
    OSError, so a bare `except OSError` would let it crash uncaught."""
    reg, skill_dir = _registry_with_skill(tmp_path)
    (skill_dir / "assets").mkdir()
    (skill_dir / "assets" / "logo.png").write_bytes(b"\xff\xd8\xff\xe0\x00\x10")

    plugin = SkillToolsPlugin()
    result = plugin.on_execute_tool(
        "read_skill_file", {"skill": "my-skill", "path": "assets/logo.png"},
        ctx=_FakeCtx(reg),
    )
    assert "error" in result
    assert "Cannot read" in result["error"]


def test_read_skill_file_via_injected_backend_plugin(tmp_path: Path):
    """End-to-end: read_skill_file must also work when routed through a
    ctx.skill_backend_plugin (previously only run_skill_script had test
    coverage for the injected-backend code path)."""
    reg, _ = _registry_with_skill(tmp_path)

    # Isolated base_dir for the backend plugin: agentskills.Discovery does an
    # ancestor-dir walk, so if it shared tmp_path with the registry fixture's
    # <tmp_path>/my-skill/ dir above, it would resolve the same skill name to
    # THAT directory instead of the backend's own <backend_root>/skills/my-skill/.
    backend_root = tmp_path / "backend_root"
    backend_skill_dir = backend_root / "skills" / "my-skill"
    backend_refs_dir = backend_skill_dir / "references"
    backend_refs_dir.mkdir(parents=True)
    (backend_skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: Does something.\n---\n# Body\n",
        encoding="utf-8",
    )
    (backend_refs_dir / "rules.md").write_text("# Backend rules", encoding="utf-8")
    backend_plugin = NativeSkillPlugin(base_dir=backend_root)
    backend_plugin.discover(backend_root)

    ctx = _FakeCtx(reg)
    ctx.skill_backend_plugin = backend_plugin

    plugin = SkillToolsPlugin()
    result = plugin.on_execute_tool(
        "read_skill_file", {"skill": "my-skill", "path": "references/rules.md"},
        ctx=ctx,
    )
    assert "content" in result
    assert "Backend rules" in result["content"]


# ---------------------------------------------------------------------------
# on_collect_tools — schema check
# ---------------------------------------------------------------------------

def test_on_collect_tools_without_registry_advertises_nothing():
    """Default: no skill is exposed to the LLM until a registry has entries."""
    plugin = SkillToolsPlugin()
    assert plugin.on_collect_tools() == []
    assert plugin.on_collect_tools(ctx=object()) == []


def test_list_tools_returns_three_tools():
    plugin = SkillToolsPlugin()
    tools = plugin.list_tools()
    names = {t["name"] for t in tools}
    assert names == {"activate_skill", "list_skill_files", "read_skill_file"}
    by_name = {t["name"]: t for t in tools}
    assert by_name["activate_skill"]["semantics"] == {
        "concept": "skill",
        "op": "activate",
        "subject_arg": "name",
    }
    assert by_name["read_skill_file"]["semantics"]["op"] == "read"
    for tool in tools:
        assert "description" in tool
        assert "parameters" in tool
    assert "unload" not in by_name["activate_skill"]["parameters"]["properties"]


def test_list_tools_advertises_unload_param_when_enabled():
    plugin = SkillToolsPlugin()
    tools = plugin.list_tools(allow_unload=True)
    names = {t["name"] for t in tools}
    assert names == {"activate_skill", "list_skill_files", "read_skill_file"}
    activate = next(t for t in tools if t["name"] == "activate_skill")
    assert "unload" in activate["parameters"]["properties"]
    assert activate["parameters"]["properties"]["unload"]["default"] is False


def test_list_tools_has_no_enum_without_a_registry():
    """The real model-facing path (skill-access.tool.yaml) constructs this
    class with no arguments -- without ctx.skill_registry threaded through,
    activate_skill's name parameter has no schema-level constraint."""
    plugin = SkillToolsPlugin()
    tools = plugin.list_tools()
    name_schema = next(t for t in tools if t["name"] == "activate_skill")["parameters"]["properties"]["name"]
    assert "enum" not in name_schema


def test_list_tools_enum_constrains_activate_skill_to_registered_names(tmp_path: Path):
    reg, _ = _registry_with_skill(tmp_path, name="my-skill")
    plugin = SkillToolsPlugin()
    tools = plugin.list_tools(reg)
    name_schema = next(t for t in tools if t["name"] == "activate_skill")["parameters"]["properties"]["name"]
    assert name_schema["enum"] == ["my-skill"]


def test_on_collect_tools_reads_registry_from_ctx(tmp_path: Path):
    """The actual real-world call path: ManifestToolProvider.list_tools(ctx=...)
    threads ctx down to on_collect_tools -- see manifest_tool_provider.py."""
    reg, _ = _registry_with_skill(tmp_path, name="my-skill")
    plugin = SkillToolsPlugin()
    tools = plugin.on_collect_tools(ctx=_FakeCtx(reg))
    name_schema = next(t for t in tools if t["name"] == "activate_skill")["parameters"]["properties"]["name"]
    assert name_schema["enum"] == ["my-skill"]


def test_on_collect_tools_ctx_registry_overrides_static_registry(tmp_path: Path):
    ctx_reg, _ = _registry_with_skill(tmp_path, name="from-ctx")
    static_reg, _ = _registry_with_skill(tmp_path, name="from-constructor")
    plugin = SkillToolsPlugin(registry=static_reg)
    tools = plugin.on_collect_tools(ctx=_FakeCtx(ctx_reg))
    name_schema = next(t for t in tools if t["name"] == "activate_skill")["parameters"]["properties"]["name"]
    assert name_schema["enum"] == ["from-ctx"]


def test_on_collect_tools_without_ctx_falls_back_to_static_registry(tmp_path: Path):
    static_reg, _ = _registry_with_skill(tmp_path, name="from-constructor")
    plugin = SkillToolsPlugin(registry=static_reg)
    tools = plugin.on_collect_tools()
    name_schema = next(t for t in tools if t["name"] == "activate_skill")["parameters"]["properties"]["name"]
    assert name_schema["enum"] == ["from-constructor"]


# ---------------------------------------------------------------------------
# enum narrowing by activation state -- regression guard for a forced
# tool_choice loop: without this, llm_tool_choice() keeps forcing
# activate_skill until every declared skill is activated, but the enum let
# the model re-select an already-activated name forever instead of being
# steered toward the ones still missing.
# ---------------------------------------------------------------------------

def test_list_tools_enum_excludes_activated_skills(tmp_path: Path):
    reg = _registry_with_skills(tmp_path, ["skill-a", "skill-b", "skill-c"])
    plugin = SkillToolsPlugin()
    tools = plugin.list_tools(reg, activated={"skill-a"})
    name_schema = next(t for t in tools if t["name"] == "activate_skill")["parameters"]["properties"]["name"]
    assert name_schema["enum"] == ["skill-b", "skill-c"]


def test_list_tools_enum_falls_back_to_full_list_when_all_activated(tmp_path: Path):
    reg = _registry_with_skills(tmp_path, ["skill-a", "skill-b"])
    plugin = SkillToolsPlugin()
    tools = plugin.list_tools(reg, activated={"skill-a", "skill-b"})
    name_schema = next(t for t in tools if t["name"] == "activate_skill")["parameters"]["properties"]["name"]
    assert name_schema["enum"] == ["skill-a", "skill-b"]


def test_list_tools_enum_unaffected_when_nothing_activated(tmp_path: Path):
    reg = _registry_with_skills(tmp_path, ["skill-a", "skill-b"])
    plugin = SkillToolsPlugin()
    tools = plugin.list_tools(reg, activated=set())
    name_schema = next(t for t in tools if t["name"] == "activate_skill")["parameters"]["properties"]["name"]
    assert name_schema["enum"] == ["skill-a", "skill-b"]


def test_on_collect_tools_excludes_activated_skills_from_ctx_session_state(tmp_path: Path):
    """Default (unload off): drop activated names so forced tool_choice moves on."""
    reg = _registry_with_skills(tmp_path, ["skill-a", "skill-b"])
    plugin = SkillToolsPlugin()
    ctx = _FakeCtx(reg, skill_session_state=_FakeSessionState(["skill-a"]))
    tools = plugin.on_collect_tools(ctx=ctx)
    name_schema = next(t for t in tools if t["name"] == "activate_skill")["parameters"]["properties"]["name"]
    assert name_schema["enum"] == ["skill-b"]
    assert "unload" not in next(
        t for t in tools if t["name"] == "activate_skill"
    )["parameters"]["properties"]


def test_on_collect_tools_advertises_unload_param_when_enabled(tmp_path: Path):
    """Same tool; unload=true needs activated names in the enum."""
    reg = _registry_with_skills(tmp_path, ["skill-a", "skill-b"])
    plugin = SkillToolsPlugin()
    ctx = _FakeCtx(reg, skill_session_state=_FakeSessionState(["skill-a"]))
    ctx.skill_allow_unload = True
    tools = plugin.on_collect_tools(ctx=ctx)
    activate = next(t for t in tools if t["name"] == "activate_skill")
    assert {t["name"] for t in tools} == {
        "activate_skill",
        "list_skill_files",
        "read_skill_file",
    }
    assert "unload" in activate["parameters"]["properties"]
    assert activate["parameters"]["properties"]["name"]["enum"] == ["skill-a", "skill-b"]


def test_on_collect_tools_without_session_state_keeps_full_enum(tmp_path: Path):
    reg = _registry_with_skills(tmp_path, ["skill-a", "skill-b"])
    plugin = SkillToolsPlugin()
    tools = plugin.on_collect_tools(ctx=_FakeCtx(reg))
    name_schema = next(t for t in tools if t["name"] == "activate_skill")["parameters"]["properties"]["name"]
    assert name_schema["enum"] == ["skill-a", "skill-b"]
