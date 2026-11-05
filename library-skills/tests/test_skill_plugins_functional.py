#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Functional tests for skill plugin implementations.

Tests use real temporary directories with actual SKILL.md files.
The LangChain plugin (deepagents) uses real deepagents._list_skills.
The ADK plugin uses real google.adk.skills.load_skill_from_dir.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from mas.library.skills.plugins import (
    ADKSkillPlugin,
    LangChainSkillPlugin,
    NativeSkillPlugin,
)

from .conftest import HAS_ADK, HAS_DEEPAGENTS, MISSING_EXTRA_REASON, make_skill

ADK_LOAD_PATCH = "google.adk.skills.load_skill_from_dir"


# ---------------------------------------------------------------------------
# Shared behaviour mixin — every plugin must pass these
# ---------------------------------------------------------------------------


class PluginBehaviourTests:
    """Mix-in: functional contract every SkillPlugin implementation must satisfy."""

    def make_plugin(self, base_dir: Path):  # noqa: ANN201
        raise NotImplementedError

    # Tier 1: Discovery -------------------------------------------------------

    def test_discover_returns_dict(self, skill_root):
        plugin = self.make_plugin(skill_root)
        skills = plugin.discover(skill_root)
        assert isinstance(skills, dict)
        assert len(skills) >= 1

    def test_discover_name_and_description(self, tmp_path):
        make_skill(tmp_path, "my-skill", description="Does things.")
        plugin = self.make_plugin(tmp_path)
        skills = plugin.discover(tmp_path)
        assert "my-skill" in skills
        assert skills["my-skill"].description == "Does things."

    def test_discover_full_frontmatter(self, skill_root_full):
        plugin = self.make_plugin(skill_root_full)
        skills = plugin.discover(skill_root_full)
        meta = skills.get("full-skill")
        assert meta is not None
        assert meta.allowed_tools == ["python", "bash"]
        assert meta.compatibility == ">=0.1.0"

    def test_discover_multiple_skills(self, skill_root_multi):
        plugin = self.make_plugin(skill_root_multi)
        skills = plugin.discover(skill_root_multi)
        assert {"skill-a", "skill-b", "skill-c"} <= set(skills)

    def test_discover_empty_dir(self, tmp_path):
        plugin = self.make_plugin(tmp_path)
        assert plugin.discover(tmp_path) == {}

    # Tier 2: Activation -------------------------------------------------------

    def test_activate_returns_body(self, tmp_path):
        make_skill(tmp_path, "s", description="S.")
        plugin = self.make_plugin(tmp_path)
        plugin.discover(tmp_path)
        act = plugin.activate("s")
        assert act.name == "s"
        assert len(act.body) > 0

    def test_activate_body_excludes_frontmatter(self, tmp_path):
        make_skill(tmp_path, "s", description="S.")
        plugin = self.make_plugin(tmp_path)
        plugin.discover(tmp_path)
        act = plugin.activate("s")
        assert "---" not in act.body.lstrip()

    def test_activate_unknown_skill_raises(self, tmp_path):
        plugin = self.make_plugin(tmp_path)
        plugin.discover(tmp_path)
        with pytest.raises(ValueError):
            plugin.activate("does-not-exist")

    def test_activate_lists_resources(self, skill_root_full):
        plugin = self.make_plugin(skill_root_full)
        plugin.discover(skill_root_full)
        act = plugin.activate("full-skill")
        assert any("scripts" in k for k in act.resources)
        assert any("references" in k for k in act.resources)

    # Tier 3: Resources --------------------------------------------------------

    def test_read_resource_ok(self, tmp_path):
        make_skill(tmp_path, "s", description="S.", with_reference=True)
        plugin = self.make_plugin(tmp_path)
        plugin.discover(tmp_path)
        content = plugin.read_resource("s", "references/README.md")
        assert "reference" in content.lower()

    def test_read_resource_not_found_raises(self, tmp_path):
        make_skill(tmp_path, "s", description="S.")
        plugin = self.make_plugin(tmp_path)
        plugin.discover(tmp_path)
        with pytest.raises(FileNotFoundError):
            plugin.read_resource("s", "scripts/nonexistent.py")

    def test_read_resource_path_traversal_blocked(self, tmp_path):
        make_skill(tmp_path, "s", description="S.")
        plugin = self.make_plugin(tmp_path)
        plugin.discover(tmp_path)
        with pytest.raises(ValueError):
            plugin.read_resource("s", "../../etc/passwd")

    # Execution ----------------------------------------------------------------

    def test_run_script_python_ok(self, tmp_path):
        make_skill(tmp_path, "s", description="S.", with_script_py=True)
        plugin = self.make_plugin(tmp_path)
        plugin.discover(tmp_path)
        result = plugin.run_script("s", "main.py")
        assert result["ok"] is True
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"].lower()

    def test_run_script_missing_script_raises(self, tmp_path):
        make_skill(tmp_path, "s", description="S.")
        plugin = self.make_plugin(tmp_path)
        plugin.discover(tmp_path)
        with pytest.raises(FileNotFoundError):
            plugin.run_script("s", "ghost.py")

    def test_run_script_nonzero_exit_ok_false(self, tmp_path):
        skill_dir = tmp_path / "skills" / "s"
        (skill_dir / "scripts").mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: s\ndescription: S.\n---\n", encoding="utf-8")
        (skill_dir / "scripts" / "fail.py").write_text("import sys; sys.exit(1)", encoding="utf-8")
        plugin = self.make_plugin(tmp_path)
        plugin.discover(tmp_path)
        result = plugin.run_script("s", "fail.py")
        assert result["ok"] is False
        assert result["exit_code"] == 1

    def test_run_script_args_passed(self, tmp_path):
        skill_dir = tmp_path / "skills" / "s"
        (skill_dir / "scripts").mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: s\ndescription: S.\n---\n", encoding="utf-8")
        (skill_dir / "scripts" / "echo_args.py").write_text(
            textwrap.dedent("import sys\nprint(' '.join(sys.argv[1:]))\n"),
            encoding="utf-8",
        )
        plugin = self.make_plugin(tmp_path)
        plugin.discover(tmp_path)
        result = plugin.run_script("s", "echo_args.py", args=["hello", "world"])
        assert result["ok"] is True
        assert "hello world" in result["stdout"]

    # Governance ---------------------------------------------------------------

    def test_allowed_tools_from_frontmatter(self, tmp_path):
        make_skill(tmp_path, "s", description="S.", full_frontmatter=True)
        plugin = self.make_plugin(tmp_path)
        plugin.discover(tmp_path)
        tools = plugin.allowed_tools("s")
        assert "python" in tools
        assert "bash" in tools

    def test_allowed_tools_empty_when_not_set(self, tmp_path):
        make_skill(tmp_path, "s", description="S.", full_frontmatter=False)
        plugin = self.make_plugin(tmp_path)
        plugin.discover(tmp_path)
        assert plugin.allowed_tools("s") == []


# ---------------------------------------------------------------------------
# NativeSkillPlugin functional tests
# ---------------------------------------------------------------------------


class TestNativeSkillPluginFunctional(PluginBehaviourTests):
    def make_plugin(self, base_dir: Path) -> NativeSkillPlugin:
        return NativeSkillPlugin(base_dir=base_dir)

    def test_run_script_sh(self, tmp_path):
        make_skill(tmp_path, "s", description="S.", with_script_sh=True)
        plugin = self.make_plugin(tmp_path)
        plugin.discover(tmp_path)
        result = plugin.run_script("s", "run.sh")
        assert result["ok"] is True
        assert "hello" in result["stdout"].lower()

    def test_run_script_returns_full_result_shape(self, tmp_path):
        make_skill(tmp_path, "s", description="S.", with_script_py=True)
        plugin = self.make_plugin(tmp_path)
        plugin.discover(tmp_path)
        result = plugin.run_script("s", "main.py")
        assert set(result.keys()) == {"exit_code", "stdout", "stderr", "ok"}


# ---------------------------------------------------------------------------
# LangChainSkillPlugin functional tests (deepagents + FilesystemBackend)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_DEEPAGENTS, reason=MISSING_EXTRA_REASON)
class TestLangChainSkillPluginFunctional(PluginBehaviourTests):
    """Uses real deepagents._list_skills + FilesystemBackend on real skill dirs."""

    def make_plugin(self, base_dir: Path) -> LangChainSkillPlugin:
        return LangChainSkillPlugin(base_dir=base_dir)

    def test_discover_agents_skills_dir(self, tmp_path):
        """deepagents scans /.agents/skills/ in addition to /skills/."""
        make_skill(tmp_path, "agent-skill", description="Hidden.", subdir=".agents/skills")
        plugin = self.make_plugin(tmp_path)
        skills = plugin.discover(tmp_path)
        assert "agent-skill" in skills

    def test_no_ancestor_walk(self, tmp_path):
        """deepagents does not do ancestor-dir walk; native is unique there."""
        parent = tmp_path.parent
        make_skill(parent, "parent-skill", description="Parent level.")
        plugin = self.make_plugin(tmp_path)
        skills = plugin.discover(tmp_path)
        assert "parent-skill" not in skills

    def test_license_field_populated(self, tmp_path):
        make_skill(tmp_path, "s", description="S.", full_frontmatter=True)
        plugin = self.make_plugin(tmp_path)
        skills = plugin.discover(tmp_path)
        assert skills["s"].license == "Apache-2.0"

    def test_deepagents_parses_allowed_tools_correctly(self, tmp_path):
        """deepagents parses allowed-tools to list[str] without char-iteration quirk."""
        make_skill(tmp_path, "s", description="S.", full_frontmatter=True)
        plugin = self.make_plugin(tmp_path)
        plugin.discover(tmp_path)
        tools = plugin.allowed_tools("s")
        assert tools == ["python", "bash"]

    def test_first_found_wins(self, tmp_path):
        """Same name in skills/ and .agents/skills/ → skills/ wins."""
        for prefix in ["skills", ".agents/skills"]:
            d = tmp_path / prefix / "dup"
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(
                f"---\nname: dup\ndescription: From {prefix}.\n---\n",
                encoding="utf-8",
            )
        plugin = self.make_plugin(tmp_path)
        skills = plugin.discover(tmp_path)
        assert len([k for k in skills if k == "dup"]) == 1
        assert skills["dup"].description == "From skills."

    def test_run_script_timeout(self, tmp_path):
        import subprocess as _sp

        skill_dir = tmp_path / "skills" / "s"
        (skill_dir / "scripts").mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: s\ndescription: S.\n---\n", encoding="utf-8")
        (skill_dir / "scripts" / "sleep.py").write_text("import time; time.sleep(100)", encoding="utf-8")

        plugin = self.make_plugin(tmp_path)
        plugin.discover(tmp_path)

        with patch(
            "mas.library.skills.plugins.skill_plugin_base.subprocess.run",
            side_effect=_sp.TimeoutExpired("cmd", 1),
        ):
            result = plugin.run_script("s", "sleep.py", timeout=1)

        assert result["ok"] is False
        assert result["exit_code"] == 124
        assert "timed out" in result["stderr"].lower()


# ---------------------------------------------------------------------------
# ADKSkillPlugin functional tests (real google.adk.skills)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_ADK, reason=MISSING_EXTRA_REASON)
class TestADKSkillPluginFunctional(PluginBehaviourTests):
    """Uses real google.adk.skills.load_skill_from_dir on real skill dirs."""

    def make_plugin(self, base_dir: Path) -> ADKSkillPlugin:
        return ADKSkillPlugin(base_dir=base_dir)

    def test_invalid_skill_silently_skipped(self, tmp_path):
        """ADK rejects uppercase names; such skills are silently skipped."""
        bad_dir = tmp_path / "skills" / "BadSkill"
        bad_dir.mkdir(parents=True)
        (bad_dir / "SKILL.md").write_text(
            "---\nname: BadSkill\ndescription: Invalid uppercase.\n---\n",
            encoding="utf-8",
        )
        good_dir = tmp_path / "skills" / "good-skill"
        good_dir.mkdir(parents=True)
        (good_dir / "SKILL.md").write_text("---\nname: good-skill\ndescription: Valid.\n---\n", encoding="utf-8")
        plugin = ADKSkillPlugin(base_dir=tmp_path)
        skills = plugin.discover(tmp_path)
        assert "BadSkill" not in skills
        assert "good-skill" in skills

    def test_run_script_js_uses_node(self, tmp_path):
        skill_dir = tmp_path / "skills" / "js-skill"
        (skill_dir / "scripts").mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: js-skill\ndescription: A JS skill.\n---\n", encoding="utf-8")
        (skill_dir / "scripts" / "run.js").write_text("console.log('js')", encoding="utf-8")

        plugin = self.make_plugin(tmp_path)
        plugin.discover(tmp_path)

        with patch("mas.library.skills.plugins.skill_plugin_base.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="js\n", stderr="")
            plugin.run_script("js-skill", "run.js")

        assert mock_run.call_args[0][0][0] == "node"

    def test_run_script_sh_uses_bash(self, tmp_path):
        skill_dir = tmp_path / "skills" / "sh-skill"
        (skill_dir / "scripts").mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: sh-skill\ndescription: A shell skill.\n---\n", encoding="utf-8")
        (skill_dir / "scripts" / "run.sh").write_text("#!/bin/sh\necho hello\n", encoding="utf-8")

        plugin = self.make_plugin(tmp_path)
        plugin.discover(tmp_path)

        with patch("mas.library.skills.plugins.skill_plugin_base.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="hello\n", stderr="")
            plugin.run_script("sh-skill", "run.sh")

        assert mock_run.call_args[0][0][0] == "/bin/bash"


# ---------------------------------------------------------------------------
# Cross-plugin consistency tests
# ---------------------------------------------------------------------------


class TestCrossPluginConsistency:
    """Same skill directory → plugins must agree on name and description."""

    PLUGINS = [LangChainSkillPlugin, ADKSkillPlugin]

    @pytest.mark.skipif(not (HAS_DEEPAGENTS and HAS_ADK), reason=MISSING_EXTRA_REASON)
    def test_same_skill_discovered_by_all(self, tmp_path):
        make_skill(tmp_path, "common-skill", description="Shared skill.")
        for cls in self.PLUGINS:
            plugin = cls(base_dir=tmp_path)
            skills = plugin.discover(tmp_path)
            assert "common-skill" in skills, f"{cls.__name__} missed common-skill"
            assert skills["common-skill"].description == "Shared skill."

    @pytest.mark.skipif(not (HAS_DEEPAGENTS and HAS_ADK), reason=MISSING_EXTRA_REASON)
    def test_path_traversal_blocked_by_all(self, tmp_path):
        make_skill(tmp_path, "s", description="S.")
        for cls in self.PLUGINS:
            plugin = cls(base_dir=tmp_path)
            plugin.discover(tmp_path)
            with pytest.raises(ValueError):
                plugin.read_resource("s", "../../secret.txt")
