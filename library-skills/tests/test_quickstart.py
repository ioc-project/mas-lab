#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Quickstart integration test — verify the skill overlay changes the agent context.

This test closely follows the Tutorial 1 pattern (overlay-based skill addition)
and demonstrates the before/after effect:

  WITHOUT skills:
    - System prompt contains only the base role text
    - No skill catalog, no activate_skill tool

  WITH skills applied:
    - System prompt contains the skill catalog (name + description — tier 1)
    - The full SKILL.md body is NOT eagerly injected (progressive disclosure)
    - activate_skill("answer-expert") returns the full body (tier 2)
    - System prompt length increases (catalog was added)

All tests run without a real LLM — no API key needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from mas.ctl.session.bootstrap import InstantiationOptions, instantiate_runtime
from mas.runtime.boundary.context.assemble import assemble_llm_messages

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

QUICKSTART = Path(__file__).resolve().parents[1] / "examples" / "quickstart"
AGENT_YAML = QUICKSTART / "agent.yaml"
OVERLAY_YAML = QUICKSTART / "overlays" / "with-skills.yaml"
SKILL_DIR = QUICKSTART / "skills"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _manifest_base() -> dict:
    """Base agent manifest — no skills.

    agent.yaml itself is the real quickstart example (meant to run against a
    live model). These tests only inspect prompt assembly/plugin wiring, never
    an actual LLM call, matching this module's "no API key needed" contract.
    """
    manifest = _load_yaml(AGENT_YAML)
    return manifest


def _manifest_with_skills() -> dict:
    """Agent manifest with answer-expert skill declared.

    Uses a direct spec.skills ref so the test resolves the skill path from
    the QUICKSTART directory without needing the pkg:// entry-point
    machinery (which works in production but requires fully installed packages).
    Tool dispatch is tested via direct plugin calls rather than going through
    ManifestToolProvider.
    """
    return {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "quickstart-agent"},
        "spec": {
            "models": [{"model": "gpt-4o-mini"}],
            "description": "Answer general knowledge questions.",
            "context": {"role": "Answer questions clearly and concisely."},
            "skills": ["answer-expert"],
            # Tools deliberately omitted here — skill-access tools are tested
            # directly via SkillToolsPlugin, not via ManifestToolProvider.
        },
    }


def _build_options(manifest: dict, manifest_dir: Path) -> InstantiationOptions:
    from mas.runtime.agent_defaults import default_pattern_plugin_id
    from mas.runtime.engine.simulated import SimulatedEngine

    return InstantiationOptions(
        pattern_plugin_id=default_pattern_plugin_id(),
        agent_manifest=manifest,
        manifest_dir=manifest_dir,
        app_root=manifest_dir,
        engine=SimulatedEngine(llm_next_step=lambda _cid: "STOP"),
        validate_manifests=False,
        enable_observability=False,
        enable_governance=False,
        enable_coordination=False,
    )


def _system_prompt(instance) -> str:
    ctx = instance.driver.ctx
    messages = assemble_llm_messages(ctx)
    sys_msg = next((m for m in messages if m.get("role") == "system"), None)
    return sys_msg["content"] if sys_msg else ""


# ---------------------------------------------------------------------------
# 1. Example files structure
# ---------------------------------------------------------------------------


class TestExampleStructure:
    def test_agent_yaml_exists(self):
        assert AGENT_YAML.is_file()

    def test_overlay_yaml_exists(self):
        assert OVERLAY_YAML.is_file()

    def test_skill_md_exists(self):
        assert (SKILL_DIR / "answer-expert" / "SKILL.md").is_file()

    def test_base_agent_has_no_skills(self):
        m = _load_yaml(AGENT_YAML)
        spec = m.get("spec") or {}
        skills = spec.get("skills")
        assert not skills, "base agent.yaml must not declare skills"

    def test_overlay_adds_skill_ref(self):
        ov = _load_yaml(OVERLAY_YAML)
        patch = (ov.get("spec") or {}).get("patch") or {}
        # spec.skills should appear in the overlay patch
        assert patch.get("skills") is not None

    def test_overlay_does_not_need_tool_ref(self):
        ov = _load_yaml(OVERLAY_YAML)
        patch = (ov.get("spec") or {}).get("patch") or {}
        assert "tools" not in patch

    def test_skill_md_frontmatter(self):
        from mas.library.skills.lib.frontmatter import parse_skill_frontmatter

        text = (SKILL_DIR / "answer-expert" / "SKILL.md").read_text(encoding="utf-8")
        meta, body = parse_skill_frontmatter(text)
        assert meta.get("name") == "answer-expert"
        assert meta.get("description"), "description required for catalog"
        assert "activate_skill" in meta["description"]
        assert len(body.strip()) > 100, "skill body should have substantive content"

    def test_skill_body_contains_format_rules(self):
        from mas.library.skills.lib.frontmatter import parse_skill_frontmatter

        text = (SKILL_DIR / "answer-expert" / "SKILL.md").read_text(encoding="utf-8")
        _, body = parse_skill_frontmatter(text)
        assert "Format rules" in body or "Rules" in body


# ---------------------------------------------------------------------------
# 2. Without skills — baseline
# ---------------------------------------------------------------------------


class TestWithoutSkills:
    @pytest.fixture(autouse=True)
    def _setup(self):
        options = _build_options(_manifest_base(), QUICKSTART)
        self.instance, _ = instantiate_runtime(options)

    def test_no_skill_catalog_in_prompt(self):
        prompt = _system_prompt(self.instance)
        assert "Available Skills" not in prompt
        assert "answer-expert" not in prompt

    def test_plugin_collection_has_no_skill_parts(self):
        ctx = self.instance.driver.ctx
        from mas.runtime.contracts.context_contract import ContextPlacement

        collection = getattr(ctx, "plugin_collection", None)
        skill_parts = []
        if collection:
            parts = collection.collect_results("collect_context")
            skill_parts = [p for p in parts if getattr(p, "placement", None) == ContextPlacement.SYSTEM_SKILLS]
        assert len(skill_parts) == 0

    def test_no_activate_skill_tool(self):
        from mas.runtime.engine.leaf import leaf_engine

        leaf = leaf_engine(self.instance.driver.engine)
        provider = getattr(leaf, "tool_provider", None)
        names = set()
        if provider is not None:
            names = {t["name"] for t in provider.list_tools(ctx=self.instance.driver.ctx)}
        assert "activate_skill" not in names
        assert "run_skill_script" not in names
        assert getattr(leaf, "use_tool_loop", False) is False


# ---------------------------------------------------------------------------
# 3. With skills — different, richer context
# ---------------------------------------------------------------------------


class TestWithSkills:
    @pytest.fixture(autouse=True)
    def _setup(self):
        options = _build_options(_manifest_with_skills(), QUICKSTART)
        self.instance, _ = instantiate_runtime(options)
        self.ctx = self.instance.driver.ctx

    # -- Tier 1: catalog in system prompt ------------------------------------

    def test_skill_catalog_in_system_prompt(self):
        """Tier 1: catalog (name + description) appears in the assembled system prompt."""
        prompt = _system_prompt(self.instance)
        assert "Available Skills" in prompt, "catalog header must be in system prompt"
        assert "answer-expert" in prompt, "skill name must be in catalog"

    def test_description_in_catalog(self):
        prompt = _system_prompt(self.instance)
        assert "activate_skill" in prompt
        assert any(kw in prompt.lower() for kw in ["factual", "knowledge"]), (
            "skill when-to-use description must appear in the catalog"
        )
        assert "HIGH / MEDIUM / LOW" not in prompt
        assert "one-sentence summary" not in prompt.lower()

    def test_full_body_not_in_system_prompt(self):
        """Tier 1 only — full SKILL.md body NOT eagerly injected."""
        prompt = _system_prompt(self.instance)
        # These strings appear only in the body, not in name/description
        assert "Anti-patterns" not in prompt
        assert "## Format rules" not in prompt

    def test_skill_registry_populated(self):
        registry = getattr(self.ctx, "skill_registry", None)
        assert registry is not None, "skill_registry must be set by bootstrap"
        record = registry.get("answer-expert")
        assert record is not None
        assert record.path.is_file()
        assert record.description

    def test_plugin_collection_contains_catalog_plugin(self):
        collection = getattr(self.ctx, "plugin_collection", None)
        assert collection is not None
        from mas.library.skills.plugins.sk_catalog import SkillCatalogPlugin

        assert len(collection.get_plugins_by_type(SkillCatalogPlugin)) == 1

    def test_activate_skill_is_a_system_tool(self):
        from mas.ctl.session.manifest_config import engine_use_tool_loop, kernel_config_from_manifest
        from mas.runtime.engine.leaf import leaf_engine

        manifest = _manifest_with_skills()
        kernel = kernel_config_from_manifest(manifest)
        assert engine_use_tool_loop(manifest, kernel) is True
        leaf = leaf_engine(self.instance.driver.engine)
        names = {t["name"] for t in leaf.tool_provider.list_tools(ctx=self.ctx)}
        assert "activate_skill" in names
        assert "run_skill_script" not in names

    # -- Tier 2: activate_skill tool -----------------------------------------

    def test_activate_skill_returns_body(self):
        """Tier 2: activate_skill returns full SKILL.md body (frontmatter stripped)."""
        from mas.library.skills.plugins.sk_tools import SkillToolsPlugin

        plugin = SkillToolsPlugin()
        result = plugin.on_execute_tool("activate_skill", {"name": "answer-expert"}, ctx=self.ctx)
        assert "error" not in result, f"unexpected error: {result.get('error')}"
        content = result["content"]
        assert '<skill_content name="answer-expert">' in content
        assert "Format rules" in content or "Rules" in content  # body present
        assert "---" not in content  # frontmatter stripped
        assert "name: answer-expert" not in content

    def test_activate_skill_unknown_returns_error(self):
        from mas.library.skills.plugins.sk_tools import SkillToolsPlugin

        plugin = SkillToolsPlugin()
        result = plugin.on_execute_tool("activate_skill", {"name": "ghost-skill"}, ctx=self.ctx)
        assert "error" in result

    # -- Key assertion: system prompt DIFFERS from baseline ------------------

    def test_system_prompt_differs_from_baseline(self):
        """The system prompt WITH skills must differ from WITHOUT — observable change."""
        base_opts = _build_options(_manifest_base(), QUICKSTART)
        base_instance, _ = instantiate_runtime(base_opts)
        base_prompt = _system_prompt(base_instance)
        skills_prompt = _system_prompt(self.instance)

        assert skills_prompt != base_prompt, "system prompt must differ when skills are applied"
        assert len(skills_prompt) > len(base_prompt), "skills prompt must be longer (catalog was added)"

    def test_collect_context_returns_skills_part(self):
        """plugin_collection.collect_results('collect_context') returns ContextPart."""
        from mas.runtime.contracts.context_contract import ContextPlacement

        collection = getattr(self.ctx, "plugin_collection", None)
        assert collection is not None
        parts = collection.collect_results("collect_context")
        skill_parts = [p for p in parts if getattr(p, "placement", None) == ContextPlacement.SYSTEM_SKILLS]
        assert len(skill_parts) == 1
        assert skill_parts[0].pinned is True  # catalog is pinned
