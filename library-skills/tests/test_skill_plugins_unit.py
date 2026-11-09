#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Unit tests for skill plugin implementations.

Most tests mock external dependencies (agentskills, google-adk, deepagents)
and run without optional extras installed. `TestADKSkillPlugin` and
`TestLangChainSkillPlugin` use ``unittest.mock.patch("module.path...")`` on
real google-adk/deepagents attributes (except their ``test_init_requires_*``
cases) and therefore require the ``library-skills[all]`` extra; they are
skipped otherwise.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from mas.library.skills.plugins import (
    ADKSkillPlugin,
    LangChainSkillPlugin,
    NativeSkillPlugin,
    SkillActivation,
    SkillMetadata,
    SkillPlugin,
    SkillPluginRegistry,
)
from mas.library.skills.plugins.plugin_skills_native import _SkillEntry

from .conftest import HAS_ADK, HAS_DEEPAGENTS, MISSING_EXTRA_REASON

# ---------------------------------------------------------------------------
# SkillPlugin abstract base
# ---------------------------------------------------------------------------


class TestSkillPluginABC:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            SkillPlugin()  # type: ignore[abstract]

    def test_all_abstract_methods_declared(self):
        expected = {"discover", "activate", "read_resource", "run_script", "allowed_tools"}
        assert SkillPlugin.__abstractmethods__ == expected


# ---------------------------------------------------------------------------
# SkillMetadata / SkillActivation dataclasses
# ---------------------------------------------------------------------------


class TestDataclasses:
    def test_metadata_defaults(self):
        meta = SkillMetadata(name="x", description="y", path=Path("/p"))
        assert meta.license is None
        assert meta.compatibility is None
        assert meta.allowed_tools is None
        assert meta.metadata_dict is None

    def test_metadata_full(self):
        meta = SkillMetadata(
            name="my-skill",
            description="Does things.",
            path=Path("/skills/my-skill"),
            license="Apache-2.0",
            compatibility=">=0.1",
            allowed_tools=["python", "bash"],
        )
        assert meta.license == "Apache-2.0"
        assert meta.allowed_tools == ["python", "bash"]

    def test_activation_fields(self):
        act = SkillActivation(
            name="my-skill",
            body="## Instructions",
            resources={"scripts/main.py": Path("/scripts/main.py")},
        )
        assert act.name == "my-skill"
        assert "Instructions" in act.body
        assert "scripts/main.py" in act.resources


# ---------------------------------------------------------------------------
# SkillPluginRegistry
# ---------------------------------------------------------------------------


class TestSkillPluginRegistry:
    def test_enum_by_name(self):
        reg = SkillPluginRegistry(impl="native")
        assert reg.impl == "native"

    def test_enum_by_value(self):
        reg = SkillPluginRegistry(impl="langchain")
        assert reg.impl == "langchain"

    def test_invalid_impl_raises(self):
        with pytest.raises(ValueError):
            SkillPluginRegistry(impl="does-not-exist")

    def test_available_implementations(self):
        impls = SkillPluginRegistry.available_implementations()
        assert set(impls) >= {"native", "langchain", "adk"}
        assert "llamaindex" not in impls

    def test_available_implementations_come_from_plugin_registry(self):
        from mas.runtime.registry import get_registry

        assert set(SkillPluginRegistry.available_implementations()) == set(get_registry().list_names("skill_impl"))

    def test_repr(self):
        reg = SkillPluginRegistry(impl="adk")
        assert "adk" in repr(reg)

    def test_native_get_plugin(self, tmp_path):
        reg = SkillPluginRegistry(impl="native")
        plugin = reg.get_plugin(base_dir=tmp_path)
        assert isinstance(plugin, NativeSkillPlugin)

    def test_langchain_get_plugin(self, tmp_path):
        if not HAS_DEEPAGENTS:
            pytest.skip(MISSING_EXTRA_REASON)
        with patch("deepagents.backends.FilesystemBackend"):
            with patch("deepagents.middleware.skills._list_skills"):
                reg = SkillPluginRegistry(impl="langchain")
                plugin = reg.get_plugin(base_dir=tmp_path)
                assert isinstance(plugin, LangChainSkillPlugin)

    def test_adk_get_plugin_mocked(self, tmp_path):
        if not HAS_ADK:
            pytest.skip(MISSING_EXTRA_REASON)
        with patch("google.adk.skills.load_skill_from_dir"):
            reg = SkillPluginRegistry(impl="adk")
            plugin = reg.get_plugin(base_dir=tmp_path)
            assert isinstance(plugin, ADKSkillPlugin)


# ---------------------------------------------------------------------------
# NativeSkillPlugin
# ---------------------------------------------------------------------------


def _make_entry(
    skill_dir: Path,
    name: str | None = None,
    body: str = "Body.",
    allowed_tools: list[str] | None = None,
) -> _SkillEntry:
    """Build a _SkillEntry for direct injection into plugin._cache."""
    n = name or skill_dir.name
    return _SkillEntry(
        metadata=SkillMetadata(
            name=n,
            description=f"{n} description",
            path=skill_dir,
            compatibility=">=0.1",
            allowed_tools=allowed_tools,
        ),
        body=body,
        resources={},
    )


class TestNativeSkillPlugin:
    def test_discover_uses_agentskills_discovery(self, tmp_path):
        """discover() delegates to agentskills.Discovery and parses frontmatter."""
        skill_dir = tmp_path / "skills" / "qa"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: qa\ndescription: QA.\ncompatibility: '>=0.1'\nallowed-tools: python bash\n---\n",
            encoding="utf-8",
        )

        mock_record = MagicMock()
        mock_record.name = "qa"
        mock_record.description = "QA."
        mock_record.path = skill_dir / "SKILL.md"
        mock_record.license = None
        mock_record.compatibility = ">=0.1"
        mock_registry = MagicMock()
        mock_registry.all.return_value = [mock_record]

        plugin = NativeSkillPlugin(base_dir=tmp_path)
        with patch("mas.library.skills.plugins.plugin_skills_native.Discovery") as mock_disc:
            mock_disc.return_value.discover.return_value = mock_registry
            skills = plugin.discover(tmp_path)

        assert "qa" in skills
        assert skills["qa"].compatibility == ">=0.1"
        # allowed-tools parsed correctly from our own parse (not char-by-char)
        assert set(skills["qa"].allowed_tools or []) == {"python", "bash"}

    def test_activate_returns_cached_body(self, tmp_path):
        """activate() returns pre-cached body — no extra disk read."""
        skill_dir = tmp_path / "skills" / "qa"
        skill_dir.mkdir(parents=True)

        plugin = NativeSkillPlugin(base_dir=tmp_path)
        plugin._cache = {"qa": _make_entry(skill_dir, "qa", body="Cached body.")}

        act = plugin.activate("qa")
        assert act.body == "Cached body."

    def test_activate_missing_skill_raises(self, tmp_path):
        plugin = NativeSkillPlugin(base_dir=tmp_path)
        plugin._cache = {}
        with pytest.raises(ValueError, match="not found"):
            plugin.activate("ghost")

    def test_read_resource_path_traversal_blocked(self, tmp_path):
        skill_dir = (tmp_path / "skills" / "qa").resolve()
        skill_dir.mkdir(parents=True)

        plugin = NativeSkillPlugin(base_dir=tmp_path)
        plugin._cache = {"qa": _make_entry(skill_dir, "qa")}

        with pytest.raises(ValueError, match="Path traversal"):
            plugin.read_resource("qa", "../../../etc/passwd")

    def test_allowed_tools_from_cache(self, tmp_path):
        """allowed_tools() reads from cache — no disk read."""
        skill_dir = tmp_path / "skills" / "qa"
        skill_dir.mkdir(parents=True)

        plugin = NativeSkillPlugin(base_dir=tmp_path)
        plugin._cache = {"qa": _make_entry(skill_dir, "qa", allowed_tools=["python", "bash"])}
        assert set(plugin.allowed_tools("qa")) == {"python", "bash"}

    def test_allowed_tools_empty_when_not_set(self, tmp_path):
        plugin = NativeSkillPlugin(base_dir=tmp_path)
        plugin._cache = {"qa": _make_entry(tmp_path / "skills" / "qa", "qa")}
        assert plugin.allowed_tools("qa") == []

    def test_run_script_calls_sandbox(self, tmp_path):
        """run_script() delegates to sandbox.run_script() with POSIX rlimit."""
        skill_dir = tmp_path / "skills" / "qa"
        (skill_dir / "scripts").mkdir(parents=True)
        (skill_dir / "scripts" / "main.py").write_text("print('ok')", encoding="utf-8")

        plugin = NativeSkillPlugin(base_dir=tmp_path)
        plugin._cache = {
            "qa": _SkillEntry(
                metadata=SkillMetadata(name="qa", description="QA.", path=skill_dir),
                body="",
                resources={"scripts/main.py": skill_dir / "scripts" / "main.py"},
            )
        }

        mock_result = MagicMock(exit_code=0, stdout="ok\n", stderr="", ok=True)
        with patch(
            "mas.library.skills.plugins.plugin_skills_native.run_script",
            return_value=mock_result,
        ) as mock_run:
            result = plugin.run_script("qa", "main.py")

        mock_run.assert_called_once()
        assert result["ok"] is True


# ---------------------------------------------------------------------------
# LangChainSkillPlugin (deepagents) — real API, mocked at _list_skills
# ---------------------------------------------------------------------------


_DEEPAGENTS_SKILL = {
    "name": "my-skill",
    "description": "A skill.",
    "path": "/skills/my-skill/SKILL.md",
    "metadata": {},
    "license": None,
    "compatibility": None,
    "allowed_tools": ["python", "bash"],
}

_LIST_SKILLS_PATCH = "deepagents.middleware.skills._list_skills"
_BACKEND_READ_PATCH = "mas.library.skills.plugins.plugin_skills_langchain._backend_read"


@pytest.mark.skipif(not HAS_DEEPAGENTS, reason=MISSING_EXTRA_REASON)
class TestLangChainSkillPlugin:
    """LangChain plugin uses deepagents._list_skills + FilesystemBackend."""

    def _make_plugin(self, tmp_path: Path) -> LangChainSkillPlugin:
        with patch("deepagents.backends.FilesystemBackend"):
            with patch("deepagents.middleware.skills._list_skills"):
                return LangChainSkillPlugin(base_dir=tmp_path)

    def test_init_requires_deepagents(self):
        with patch.dict(
            sys.modules,
            {
                "deepagents": None,
                "deepagents.backends": None,
                "deepagents.middleware": None,
                "deepagents.middleware.skills": None,
            },
        ):
            with pytest.raises((ImportError, TypeError)):
                LangChainSkillPlugin()

    def test_discover_calls_list_skills_per_path(self, tmp_path):
        plugin = self._make_plugin(tmp_path)
        with patch(_LIST_SKILLS_PATCH, return_value=[_DEEPAGENTS_SKILL]) as mock_ls:
            skills = plugin.discover(tmp_path)
        assert mock_ls.call_count >= 1
        assert "my-skill" in skills

    def test_discover_metadata_from_deepagents(self, tmp_path):
        plugin = self._make_plugin(tmp_path)
        skill = {**_DEEPAGENTS_SKILL, "license": "MIT", "compatibility": ">=1.0"}
        with patch(_LIST_SKILLS_PATCH, return_value=[skill]):
            skills = plugin.discover(tmp_path)
        meta = skills["my-skill"]
        assert meta.allowed_tools == ["python", "bash"]
        assert meta.license == "MIT"
        assert meta.compatibility == ">=1.0"

    def test_discover_invalid_skills_skipped(self, tmp_path):
        plugin = self._make_plugin(tmp_path)
        with patch(_LIST_SKILLS_PATCH, side_effect=Exception("scan error")):
            skills = plugin.discover(tmp_path)
        assert skills == {}

    def test_activate_reads_via_backend(self, tmp_path):
        plugin = self._make_plugin(tmp_path)
        with patch(_LIST_SKILLS_PATCH, return_value=[_DEEPAGENTS_SKILL]):
            plugin.discover(tmp_path)
        body_content = "---\nname: my-skill\ndescription: A skill.\n---\nThe instructions."
        with patch(_BACKEND_READ_PATCH, return_value=body_content):
            with patch.object(plugin, "_list_resources", return_value={}):
                act = plugin.activate("my-skill")
        assert "instructions" in act.body
        assert "---" not in act.body.lstrip()

    def test_activate_unknown_skill_raises(self, tmp_path):
        plugin = self._make_plugin(tmp_path)
        with patch(_LIST_SKILLS_PATCH, return_value=[]):
            plugin.discover(tmp_path)
        with pytest.raises(ValueError, match="not found"):
            plugin.activate("ghost")

    def test_read_resource_path_traversal_blocked(self, tmp_path):
        plugin = self._make_plugin(tmp_path)
        with patch(_LIST_SKILLS_PATCH, return_value=[_DEEPAGENTS_SKILL]):
            plugin.discover(tmp_path)
        with pytest.raises(ValueError, match="category/name"):
            plugin.read_resource("my-skill", "../../etc/passwd")

    def test_read_resource_calls_backend_read(self, tmp_path):
        plugin = self._make_plugin(tmp_path)
        with patch(_LIST_SKILLS_PATCH, return_value=[_DEEPAGENTS_SKILL]):
            plugin.discover(tmp_path)
        with patch(_BACKEND_READ_PATCH, return_value="# reference") as mock_read:
            content = plugin.read_resource("my-skill", "references/doc.md")
        mock_read.assert_called_once_with(
            plugin._get_backend(),
            "/skills/my-skill/references/doc.md",
        )
        assert content == "# reference"

    def test_run_script_reads_via_backend_then_executes(self, tmp_path):
        plugin = self._make_plugin(tmp_path)
        with patch(_LIST_SKILLS_PATCH, return_value=[_DEEPAGENTS_SKILL]):
            plugin.discover(tmp_path)
        with patch(_BACKEND_READ_PATCH, return_value="print('hello from deepagents')"):
            with patch("mas.library.skills.plugins.skill_plugin_base.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="hello\n", stderr="")
                result = plugin.run_script("my-skill", "main.py")
        assert result["ok"] is True
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == sys.executable
        assert cmd[1] != "main.py"
        assert cmd[1].endswith(".py")

    def test_run_script_rejects_path_traversal_in_script_name(self, tmp_path):
        """FilesystemBackend is rooted at base_dir (shared across all
        skills, not just this one) -- a `..` in script_name must be
        rejected before it's concatenated into the virtual path string."""
        plugin = self._make_plugin(tmp_path)
        with patch(_LIST_SKILLS_PATCH, return_value=[_DEEPAGENTS_SKILL]):
            plugin.discover(tmp_path)
        with pytest.raises(ValueError, match="Path traversal"):
            plugin.run_script("my-skill", "../../etc/passwd")

    def test_allowed_tools_from_deepagents(self, tmp_path):
        plugin = self._make_plugin(tmp_path)
        with patch(_LIST_SKILLS_PATCH, return_value=[_DEEPAGENTS_SKILL]):
            plugin.discover(tmp_path)
        assert plugin.allowed_tools("my-skill") == ["python", "bash"]


# ---------------------------------------------------------------------------
# ADKSkillPlugin — real google.adk.skills API, mocked at load_skill_from_dir
# ---------------------------------------------------------------------------


def _make_adk_skill_mock(name: str, description: str, body: str = "Instructions.") -> MagicMock:
    """Build a mock mirroring google.adk.skills.models.Skill structure."""
    skill = MagicMock()
    skill.name = name
    skill.description = description
    skill.instructions = body
    skill.frontmatter.name = name
    skill.frontmatter.description = description
    skill.frontmatter.license = None
    skill.frontmatter.compatibility = None
    skill.frontmatter.allowed_tools = None
    skill.frontmatter.metadata = {}
    skill.resources.list_references.return_value = []
    skill.resources.list_assets.return_value = []
    skill.resources.list_scripts.return_value = []
    skill.resources.get_reference.return_value = None
    skill.resources.get_asset.return_value = None
    skill.resources.get_script.return_value = None
    return skill


ADK_LOAD_PATCH = "google.adk.skills.load_skill_from_dir"


@pytest.mark.skipif(not HAS_ADK, reason=MISSING_EXTRA_REASON)
class TestADKSkillPlugin:
    """ADK plugin wraps google.adk.skills.load_skill_from_dir + Skill model."""

    def test_init_requires_adk(self):
        with patch.dict(sys.modules, {"google": None, "google.adk": None, "google.adk.skills": None}):
            with pytest.raises((ImportError, TypeError)):
                ADKSkillPlugin()

    def test_discover_calls_load_skill_from_dir_per_subdir(self, tmp_path):
        skill_dir = tmp_path / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: my-skill\ndescription: A skill.\n---\nBody.", encoding="utf-8")
        mock_skill = _make_adk_skill_mock("my-skill", "A skill.", "Body.")
        with patch(ADK_LOAD_PATCH, return_value=mock_skill) as mock_loader:
            plugin = ADKSkillPlugin(base_dir=tmp_path)
            plugin._discover_with(tmp_path, mock_loader)
        mock_loader.assert_called_once_with(skill_dir)
        assert "my-skill" in plugin._skills

    def test_discover_skips_adk_validation_failures(self, tmp_path):
        skill_dir = tmp_path / "skills" / "bad"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: bad\ndescription: Bad.\n---\n", encoding="utf-8")
        plugin = ADKSkillPlugin(base_dir=tmp_path)
        plugin._discover_with(tmp_path, MagicMock(side_effect=ValueError("ADK schema error")))
        assert "bad" not in plugin._skills

    def test_activate_body_from_skill_instructions(self, tmp_path):
        skill_dir = tmp_path / "skills" / "s"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: s\ndescription: S.\n---\nDifferent body.", encoding="utf-8")
        mock_skill = _make_adk_skill_mock("s", "S.", body="ADK parsed instructions.")
        plugin = ADKSkillPlugin(base_dir=tmp_path)
        plugin._discover_with(tmp_path, MagicMock(return_value=mock_skill))
        act = plugin.activate("s")
        assert act.body == "ADK parsed instructions."

    def test_read_resource_uses_skill_resources_not_disk(self, tmp_path):
        skill_dir = tmp_path / "skills" / "s"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: s\ndescription: S.\n---\n", encoding="utf-8")
        mock_skill = _make_adk_skill_mock("s", "S.")
        mock_skill.resources.get_reference.return_value = "in-memory reference"
        plugin = ADKSkillPlugin(base_dir=tmp_path)
        plugin._discover_with(tmp_path, MagicMock(return_value=mock_skill))
        content = plugin.read_resource("s", "references/doc.md")
        mock_skill.resources.get_reference.assert_called_once_with("doc.md")
        assert content == "in-memory reference"

    def test_read_resource_rejects_path_traversal(self, tmp_path):
        skill_dir = tmp_path / "skills" / "s"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: s\ndescription: S.\n---\n", encoding="utf-8")
        mock_skill = _make_adk_skill_mock("s", "S.")
        plugin = ADKSkillPlugin(base_dir=tmp_path)
        plugin._discover_with(tmp_path, MagicMock(return_value=mock_skill))
        with pytest.raises(ValueError):
            plugin.read_resource("s", "../../etc/passwd")

    def test_run_script_writes_script_src_to_tempfile(self, tmp_path):
        skill_dir = tmp_path / "skills" / "s"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: s\ndescription: S.\n---\n", encoding="utf-8")
        mock_script = MagicMock()
        mock_script.__str__ = lambda self: "print('hello from adk script')"
        mock_skill = _make_adk_skill_mock("s", "S.")
        mock_skill.resources.get_script.return_value = mock_script
        plugin = ADKSkillPlugin(base_dir=tmp_path)
        plugin._discover_with(tmp_path, MagicMock(return_value=mock_skill))
        with patch("mas.library.skills.plugins.skill_plugin_base.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="hello\n", stderr="")
            result = plugin.run_script("s", "main.py")
        assert result["ok"] is True
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == sys.executable
        assert cmd[1] != "main.py"
        assert cmd[1].endswith(".py")

    def test_run_script_rejects_path_traversal_in_script_name(self, tmp_path):
        """Defense in depth: ADK's get_script() is a dict lookup so a `..`
        just misses, but reject it explicitly for a clean error and
        consistency with the LangChain/Native backends."""
        skill_dir = tmp_path / "skills" / "s"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: s\ndescription: S.\n---\n", encoding="utf-8")
        mock_skill = _make_adk_skill_mock("s", "S.")
        plugin = ADKSkillPlugin(base_dir=tmp_path)
        plugin._discover_with(tmp_path, MagicMock(return_value=mock_skill))
        with pytest.raises(ValueError, match="Path traversal"):
            plugin.run_script("s", "../../etc/passwd")

    def test_allowed_tools_from_frontmatter(self, tmp_path):
        skill_dir = tmp_path / "skills" / "s"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: s\ndescription: S.\n---\n", encoding="utf-8")
        mock_skill = _make_adk_skill_mock("s", "S.")
        mock_skill.frontmatter.allowed_tools = "python bash curl"
        plugin = ADKSkillPlugin(base_dir=tmp_path)
        plugin._discover_with(tmp_path, MagicMock(return_value=mock_skill))
        assert plugin.allowed_tools("s") == ["python", "bash", "curl"]
