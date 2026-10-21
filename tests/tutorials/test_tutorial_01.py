#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tutorial 01 — Building an Agent: integration tests.

Manifest validation, overlay merging, and tool execution run without an LLM.
Bootstrap uses an injected SimulatedEngine. Chat effect is checked with a
real model when OPENAI_API_KEY is set.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from ci_llm import stop_engine
from conftest import T01, load_yaml, run_cli

# ═══════════════════════════════════════════════════════════════════════════
# 1. Manifest & overlay validation (CLI)
# ═══════════════════════════════════════════════════════════════════════════


class TestManifestValidation:
    """mas-ctl validate must pass for every manifest and overlay combo."""

    def test_validate_base_agent(self):
        r = run_cli(["mas-ctl", "validate", str(T01 / "agent.yaml")])
        assert r.returncode == 0, r.stderr
        assert "OK" in r.stdout

    @pytest.mark.parametrize(
        "overlay",
        [
            "tools.yaml",
            "skills.yaml",
            "memory.yaml",
            "memory-seed.yaml",
            "context-manager.yaml",
        ],
    )
    def test_validate_with_overlay(self, overlay):
        overlay_path = T01 / "overlays" / overlay
        if not overlay_path.exists():
            pytest.skip(f"{overlay} not present")
        r = run_cli(
            [
                "mas-ctl",
                "validate",
                str(T01 / "agent.yaml"),
                "--overlay",
                str(overlay_path),
            ]
        )
        assert r.returncode == 0, r.stderr

    @pytest.mark.parametrize("overlay", ["cot.yaml", "baseline.yaml"])
    def test_mas_patch_overlays_are_well_formed(self, overlay):
        """cot.yaml and baseline.yaml are MAS-level Patch overlays — verify structure."""
        ov = load_yaml(T01 / "overlays" / overlay)
        assert ov.get("kind") == "Overlay" or ov.get("apiVersion") == "mas/v1"
        assert "spec" in ov

    def test_validate_stacked_overlays(self):
        """Validate agent with tools + skills + memory stacked."""
        args = ["mas-ctl", "validate", str(T01 / "agent.yaml")]
        for ov in ["tools.yaml", "skills.yaml", "memory.yaml"]:
            p = T01 / "overlays" / ov
            if p.exists():
                args += ["--overlay", str(p)]
        r = run_cli(args)
        assert r.returncode == 0, r.stderr


# ═══════════════════════════════════════════════════════════════════════════
# 2. Manifest structure tests (Python)
# ═══════════════════════════════════════════════════════════════════════════


class TestManifestStructure:
    """Verify the tutorial manifests have the expected shape."""

    def test_agent_yaml_structure(self):
        m = load_yaml(T01 / "agent.yaml")
        assert m["apiVersion"] == "mas/v1"
        assert m["kind"] == "Agent"
        assert "name" in m.get("metadata", {})
        spec = m["spec"]
        assert "models" in spec
        assert "context" in spec

    def test_overlays_directory_complete(self):
        """Tutorial 1 is CLI-first: dataset/experiment live in tuto 3+.
        Verify the expected overlays are all present."""
        expected = ["tools.yaml", "skills.yaml", "memory.yaml", "baseline.yaml", "cot.yaml"]
        for name in expected:
            p = T01 / "overlays" / name
            assert p.exists(), f"overlay {name} missing from tuto 01"


# ═══════════════════════════════════════════════════════════════════════════
# 2b. Tutorial 01 skills (frontmatter + activate_skill)
# ═══════════════════════════════════════════════════════════════════════════


class TestSkillsContract:
    """SKILL.md must start with YAML frontmatter; catalog lists that when-to-use text."""

    def test_skill_md_starts_with_yaml_frontmatter(self):
        from mas.library.skills.lib.frontmatter import parse_skill_frontmatter

        skill_md = T01 / "skills" / "answer-formatting" / "SKILL.md"
        text = skill_md.read_text(encoding="utf-8")
        assert text.startswith("---"), "SKILL.md must begin with YAML frontmatter"
        meta, body = parse_skill_frontmatter(text)
        assert meta.get("name") == "answer-formatting"
        description = meta.get("description") or ""
        assert description
        assert "activate_skill" in description
        assert "one-sentence summary" in body

    def test_skills_overlay_adds_answer_formatting(self):
        ov = load_yaml(T01 / "overlays" / "skills.yaml")
        patch = ov["spec"]["patch"]
        assert "skill_usage" not in (patch.get("context") or {})
        skills = patch["skills"]["$op"]["add"]
        assert "answer-formatting" in skills

    def test_skills_overlay_catalog_uses_frontmatter_description(self):
        from mas.library.skills.lib.frontmatter import parse_skill_frontmatter
        from mas.library.skills.plugins.sk_catalog import SkillCatalogPlugin

        instance, _ = _instantiate("skills.yaml")
        plugins = instance.driver.ctx.plugin_collection.get_plugins_by_type(SkillCatalogPlugin)
        assert len(plugins) == 1
        parts = plugins[0].collect_context()
        assert parts
        content = parts[0].content
        meta, _ = parse_skill_frontmatter(
            (T01 / "skills" / "answer-formatting" / "SKILL.md").read_text(encoding="utf-8")
        )
        description = (meta.get("description") or "").strip()
        assert "answer-formatting" in content
        assert description in content
        assert "activate_skill" in content
        assert "MUST call" not in content
        assert "Do not skip this tool call" not in content
        assert "skill_usage" not in (_tutorial_agent("skills.yaml")["spec"].get("context") or {})

    def test_activate_skill_returns_body_without_frontmatter(self):
        from mas.library.skills.plugins.sk_tools import SkillToolsPlugin

        instance, _ = _instantiate("skills.yaml")
        result = SkillToolsPlugin().on_execute_tool(
            "activate_skill",
            {"name": "answer-formatting"},
            ctx=instance.driver.ctx,
        )
        assert "error" not in result, result
        content = result["content"]
        assert "one-sentence summary" in content
        assert "name: answer-formatting" not in content
        assert "---" not in content

    def test_base_agent_does_not_expose_skill_system_tools(self):
        from mas.runtime.boundary.context.assemble import assemble_llm_messages
        from mas.runtime.engine.leaf import leaf_engine

        instance, _ = _instantiate()
        leaf = leaf_engine(instance.driver.engine)
        provider = getattr(leaf, "tool_provider", None)
        names = (
            {t["name"] for t in provider.list_tools(ctx=instance.driver.ctx)}
            if provider is not None
            else set()
        )
        assert "activate_skill" not in names
        assert "run_skill_script" not in names
        assert "list_skill_files" not in names
        assert "read_skill_file" not in names
        assert getattr(leaf, "use_tool_loop", False) is False
        messages = assemble_llm_messages(instance.driver.ctx)
        prompt = next((m.get("content") or "" for m in messages if m.get("role") == "system"), "")
        assert "Available Skills" not in prompt

    def test_activate_skill_is_listed_as_system_tool(self):
        from mas.ctl.session.manifest_config import engine_use_tool_loop, kernel_config_from_manifest
        from mas.runtime.engine.leaf import leaf_engine

        manifest = _tutorial_agent("skills.yaml")
        kernel = kernel_config_from_manifest(manifest)
        assert engine_use_tool_loop(manifest, kernel) is True
        instance, _ = _instantiate("skills.yaml")
        leaf = leaf_engine(instance.driver.engine)
        names = {t["name"] for t in leaf.tool_provider.list_tools(ctx=instance.driver.ctx)}
        assert "activate_skill" in names
        assert "run_skill_script" not in names


# ═══════════════════════════════════════════════════════════════════════════
# 3. Overlay merging (Python — real overlay logic)
# ═══════════════════════════════════════════════════════════════════════════


class TestOverlayMerging:
    """Test that overlay merging produces correct merged manifests."""

    def test_tools_overlay_adds_tools(self):
        from mas.ctl.overlay import merge_overlay

        base = load_yaml(T01 / "agent.yaml")
        tools_ov = load_yaml(T01 / "overlays" / "tools.yaml")
        merged = merge_overlay(base, tools_ov)
        spec = merged.get("spec", {})
        # tools overlay should inject tools and/or context.tool_usage
        assert "tools" in spec or "tool_usage" in spec.get("context", {})

    def test_memory_overlay_adds_memory(self):
        from mas.ctl.overlay import merge_overlay

        base = load_yaml(T01 / "agent.yaml")
        mem_ov = load_yaml(T01 / "overlays" / "memory.yaml")
        merged = merge_overlay(base, mem_ov)
        spec = merged.get("spec", {})
        assert "memory" in spec or "tools" in spec

    def test_stacked_overlays_cumulative(self):
        from mas.ctl.overlay import merge_overlay

        base = load_yaml(T01 / "agent.yaml")
        for ov_name in ["tools.yaml", "skills.yaml", "memory.yaml"]:
            ov_path = T01 / "overlays" / ov_name
            if ov_path.exists():
                ov = load_yaml(ov_path)
                base = merge_overlay(base, ov)
        spec = base.get("spec", {})
        # After all overlays, should have tools + skills + memory
        has_tools = "tools" in spec
        has_memory = "memory" in spec
        assert has_tools or has_memory, f"Expected tools/memory in merged spec: {list(spec.keys())}"

    def test_cot_overlay_changes_design_pattern(self):
        from mas.ctl.overlay import merge_overlay

        base = load_yaml(T01 / "agent.yaml")
        cot_ov = load_yaml(T01 / "overlays" / "cot.yaml")
        merged = merge_overlay(base, cot_ov)
        # CoT overlay should have set a design pattern somewhere
        # It may be under spec.patch or directly in spec
        merged_str = yaml.dump(merged)
        assert "cot" in merged_str.lower()


# ═══════════════════════════════════════════════════════════════════════════
# 4. Agent instantiation (default runtime — injected SimulatedEngine)
# ═══════════════════════════════════════════════════════════════════════════


def _tutorial_agent(*extra_overlays: str) -> dict:
    """Tutorial agent.yaml plus optional overlays. Does not stack a fake LLM."""
    from mas.ctl.overlay import merge_overlay

    base = load_yaml(T01 / "agent.yaml")
    for name in extra_overlays:
        base = merge_overlay(base, load_yaml(T01 / "overlays" / name))
    return base


def _instantiate(*extra_overlays: str):
    from mas.ctl.session.bootstrap import InstantiationOptions, instantiate_runtime

    return instantiate_runtime(
        InstantiationOptions(
            agent_manifest=_tutorial_agent(*extra_overlays),
            manifest_dir=T01,
            validate_manifests=False,
            engine=stop_engine(),
        )
    )


class TestAgentInstantiation:
    """Instantiate an agent from manifest via mas-ctl session bootstrap."""

    def test_instantiate_base_agent(self):
        config = _tutorial_agent()
        instance, _ = _instantiate()
        assert instance is not None
        assert config.get("metadata", {}).get("name") == "qa-agent"

    def test_instantiate_with_tools_overlay(self):
        instance, _ = _instantiate("tools.yaml")
        assert instance is not None

    def test_session_controller_turn(self):
        """Run one scripted turn against SimulatedEngine."""
        from mas.ctl.session.bootstrap import InstantiationOptions, instantiate_runtime
        from mas.ctl.session.controller import ConversationConfig, SessionController
        from mas.ctl.ui.stdout import StdoutConversationDisplay

        config = _tutorial_agent()
        instance, _ = instantiate_runtime(
            InstantiationOptions(
                agent_manifest=config,
                manifest_dir=T01,
                validate_manifests=False,
                engine=stop_engine(text="Paris"),
            ),
        )
        controller = SessionController(
            instance=instance,
            display=StdoutConversationDisplay(show_labels=False, verbose=0),
            config=ConversationConfig(single_turn=True),
        )
        result = controller.run_turn("What is the capital of France?")
        assert result.text
        assert "Paris" in result.text


# ═══════════════════════════════════════════════════════════════════════════
# 4b. Live mas-ctl chat (real LLM)
# ═══════════════════════════════════════════════════════════════════════════

_LIVE = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY required for live tutorial chat",
)
_LIVE_QUERY = "What is the speed of light?"


def _live_cli_env() -> dict[str, str]:
    """pytest isolates XDG_CONFIG_HOME; live chat must see the operator config."""
    return {"XDG_CONFIG_HOME": str(Path.home() / ".config")}


@_LIVE
class TestLiveSkillChat:
    """mas-ctl chat from the tutorial directory against a real model."""

    def test_without_skills_does_not_load_or_activate(self):
        r = run_cli(
            ["mas-ctl", "chat", "agent.yaml", "--no-cache-read", "--trace", "-q", _LIVE_QUERY],
            cwd=T01,
            timeout=120,
            extra_env=_live_cli_env(),
        )
        combined = f"{r.stdout}\n{r.stderr}"
        assert r.returncode == 0, r.stderr
        assert "Available Skills" not in combined
        assert "activate_skill" not in combined

    def test_with_skills_activates_and_formats(self):
        r = run_cli(
            [
                "mas-ctl",
                "chat",
                "agent.yaml",
                "-o",
                "overlays/skills.yaml",
                "--no-cache-read",
                "--trace",
                "-q",
                _LIVE_QUERY,
            ],
            cwd=T01,
            timeout=120,
            extra_env=_live_cli_env(),
        )
        combined = f"{r.stdout}\n{r.stderr}"
        assert r.returncode == 0, r.stderr
        # Summary --trace does not dump the system prompt (Available Skills).
        assert "SKILL[answer-formatting]" in combined
        assert "TOOL[activate_skill]" in combined
        assert "Confidence:" in combined
        user_out = [line for line in r.stderr.splitlines() if "-> USER" in line]
        assert user_out
        assert "..." not in user_out[-1]


# ═══════════════════════════════════════════════════════════════════════════
# 5. Built-in tools (Python — real execution)
# ═══════════════════════════════════════════════════════════════════════════


class TestBuiltinTools:
    """Test web-search and calc via library-samples manifest tool refs."""

    @staticmethod
    def _tutorial_tools_provider():

        from mas.runtime.engine.manifest_tool_provider import build_manifest_tool_provider

        root = Path(__file__).resolve().parents[2] / "docs/tutorials/01-building-an-agent"
        return build_manifest_tool_provider(
            [
                {"ref": "samples:tools/calc.tool.yaml"},
                {"ref": "samples:tools/web-search.tool.yaml"},
            ],
            root,
        )

    def test_calculator_tool(self):
        from mas.runtime.engine.tool_dispatch import execute_engine_tool

        provider = self._tutorial_tools_provider()
        result = execute_engine_tool(
            "calc",
            arguments={"expression": "9 * 3"},
            tool_provider=provider,
        )
        assert result

    def test_calculator_basic_arithmetic(self):
        from mas.runtime.engine.tool_dispatch import execute_engine_tool

        provider = self._tutorial_tools_provider()
        result = execute_engine_tool(
            "calc",
            arguments={"expression": "32768 * 2"},
            tool_provider=provider,
        )
        assert "65536" in str(result)

    def test_web_search_tool_exists(self):
        provider = self._tutorial_tools_provider()
        names = [t["name"] for t in provider.list_tools()]
        assert "web-search" in names


class TestPersistentMemory:
    """The memory-search sample tool persists to a SQLite file under the data root."""

    def test_store_survives_a_new_tool_instance(self, tmp_path, monkeypatch):
        import sys
        from types import SimpleNamespace

        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        monkeypatch.syspath_prepend(str(T01.parents[2] / "library-samples" / "tools"))
        sys.modules.pop("memory_search", None)
        from memory_search import MemorySearchTool

        ctx = SimpleNamespace(agent_id="qa-agent")
        MemorySearchTool().on_execute_tool(
            "memory_store", {"content": "User prefers Apple the fruit"}, ctx=ctx
        )
        db = tmp_path / "mas" / "memory" / "semantic" / "qa-agent.sqlite"
        assert db.is_file()
        found = MemorySearchTool().on_execute_tool("memory-search", {"query": "Apple"}, ctx=ctx)
        assert "Apple the fruit" in found["text"]

    def test_db_path_is_a_plugin_parameter(self, tmp_path):
        import inspect

        from mas.library.standard.plugins.memory.memory_semantic import SemanticMemoryPlugin

        assert "db_path" in inspect.signature(SemanticMemoryPlugin.__init__).parameters
