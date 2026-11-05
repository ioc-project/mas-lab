#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for new spec features: optional fields, .agents/skills scanning, name collisions."""

from __future__ import annotations

import logging
from pathlib import Path

# ---------------------------------------------------------------------------
# SkillRecord — optional fields
# ---------------------------------------------------------------------------


def test_skill_record_defaults():
    from mas.library.skills.lib.registry import SkillRecord

    rec = SkillRecord(name="x", description="y", path=Path("/tmp/SKILL.md"))
    assert rec.compatibility is None
    assert rec.license is None
    assert rec.tags == ()
    assert rec.source_scope == "project"


def test_skill_record_with_optional_fields():
    from mas.library.skills.lib.registry import SkillRecord

    rec = SkillRecord(
        name="pdf",
        description="PDF tools",
        path=Path("/tmp/SKILL.md"),
        compatibility="Requires Python 3.11+",
        license="Apache-2.0",
        tags=("pdf", "documents"),
        source_scope="user",
    )
    assert rec.compatibility == "Requires Python 3.11+"
    assert rec.license == "Apache-2.0"
    assert rec.tags == ("pdf", "documents")
    assert rec.source_scope == "user"


# ---------------------------------------------------------------------------
# sk_catalog.py — optional fields parsed from frontmatter
# ---------------------------------------------------------------------------


def _write_skill(path: Path, extra: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: test-skill\ndescription: Does something useful.\n{extra}---\n# Body\n",
        encoding="utf-8",
    )


def test_catalog_parses_compatibility(tmp_path: Path):
    from mas.library.skills.plugins.sk_catalog import SkillCatalogPlugin

    _write_skill(
        tmp_path / "test-skill" / "SKILL.md",
        "compatibility: Requires Python 3.11 and git\n",
    )
    plugin = SkillCatalogPlugin(
        manifest={"spec": {"skills": ["test-skill"]}},
        base_dir=tmp_path,
    )
    rec = plugin.registry.get("test-skill")
    assert rec is not None
    assert rec.compatibility == "Requires Python 3.11 and git"


def test_catalog_parses_license(tmp_path: Path):
    from mas.library.skills.plugins.sk_catalog import SkillCatalogPlugin

    _write_skill(tmp_path / "test-skill" / "SKILL.md", "license: Apache-2.0\n")
    plugin = SkillCatalogPlugin(
        manifest={"spec": {"skills": ["test-skill"]}},
        base_dir=tmp_path,
    )
    rec = plugin.registry.get("test-skill")
    assert rec.license == "Apache-2.0"


def test_catalog_parses_tags(tmp_path: Path):
    from mas.library.skills.plugins.sk_catalog import SkillCatalogPlugin

    _write_skill(tmp_path / "test-skill" / "SKILL.md", "tags: [pdf, documents]\n")
    plugin = SkillCatalogPlugin(
        manifest={"spec": {"skills": ["test-skill"]}},
        base_dir=tmp_path,
    )
    rec = plugin.registry.get("test-skill")
    assert "pdf" in rec.tags
    assert "documents" in rec.tags


# ---------------------------------------------------------------------------
# Name collision detection
# ---------------------------------------------------------------------------


def test_name_collision_warns_and_first_wins(tmp_path: Path, caplog):
    """When two refs resolve to the same name, first wins + warning logged."""
    from mas.library.skills.plugins.sk_catalog import SkillCatalogPlugin

    # Two skill directories with the same name field
    for d in ("skills-a/my-skill", "skills-b/my-skill"):
        p = tmp_path / d / "SKILL.md"
        p.parent.mkdir(parents=True)
        p.write_text(
            f"---\nname: my-skill\ndescription: Version from {d}.\n---\nBody.\n",
            encoding="utf-8",
        )

    manifest = {"spec": {"skills": ["skills-a/my-skill", "skills-b/my-skill"]}}
    with caplog.at_level(logging.WARNING, logger="mas.library.skills"):
        plugin = SkillCatalogPlugin(manifest=manifest, base_dir=tmp_path)

    # Only one entry (first wins)
    assert len(plugin.registry) == 1
    rec = plugin.registry.get("my-skill")
    assert rec is not None
    # Warning was logged
    assert any("collision" in r.message.lower() for r in caplog.records)
    # First ref wins (skills-a)
    assert "skills-a" in str(rec.path)


# ---------------------------------------------------------------------------
# resolver.py — .agents/skills/ and user-level scanning
# ---------------------------------------------------------------------------


def test_search_roots_includes_agents_skills(tmp_path: Path):
    from mas.library.skills.lib.resolver import skill_search_roots

    # Create the .agents/skills dir so it's included
    agents_dir = tmp_path / ".agents" / "skills"
    agents_dir.mkdir(parents=True)
    roots = skill_search_roots(tmp_path)
    root_strs = [str(r) for r in roots]
    assert any(".agents" in s for s in root_strs)


def test_resolve_via_agents_skills(tmp_path: Path):
    """Skill in .agents/skills/ is discoverable."""
    from mas.library.skills.lib.resolver import resolve_skill_path

    skill_dir = tmp_path / ".agents" / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: my-skill\ndescription: x\n---\nBody\n", encoding="utf-8")
    path = resolve_skill_path("my-skill", base_dir=tmp_path)
    assert path is not None
    assert path.name == "SKILL.md"
    assert ".agents" in str(path)


# ---------------------------------------------------------------------------
# activate_skill — compatibility field in response
# ---------------------------------------------------------------------------


def test_activate_skill_includes_compatibility(tmp_path: Path):
    from mas.library.skills.lib.registry import SkillRecord, SkillRegistry
    from mas.library.skills.plugins.sk_tools import SkillToolsPlugin

    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\nname: my-skill\ndescription: x\ncompatibility: Requires git\n---\nBody\n",
        encoding="utf-8",
    )
    reg = SkillRegistry()
    reg.register(
        SkillRecord(
            name="my-skill",
            description="x",
            path=skill_md,
            compatibility="Requires git",
        )
    )

    class _Ctx:
        skill_registry = reg
        skill_session_state = None

    plugin = SkillToolsPlugin()
    result = plugin.on_execute_tool("activate_skill", {"name": "my-skill"}, ctx=_Ctx())
    assert "error" not in result
    assert result.get("compatibility") == "Requires git"


def test_activate_skill_no_compatibility_key_when_absent(tmp_path: Path):
    from mas.library.skills.lib.registry import SkillRecord, SkillRegistry
    from mas.library.skills.plugins.sk_tools import SkillToolsPlugin

    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("---\nname: my-skill\ndescription: x\n---\nBody\n", encoding="utf-8")
    reg = SkillRegistry()
    reg.register(SkillRecord(name="my-skill", description="x", path=skill_md))

    class _Ctx:
        skill_registry = reg
        skill_session_state = None

    plugin = SkillToolsPlugin()
    result = plugin.on_execute_tool("activate_skill", {"name": "my-skill"}, ctx=_Ctx())
    assert "compatibility" not in result  # key absent when no compatibility set


# ---------------------------------------------------------------------------
# SkillToolsPlugin(registry=…) — dynamic tool description
# ---------------------------------------------------------------------------


def test_list_tools_with_registry_includes_names(tmp_path: Path):
    from mas.library.skills.lib.registry import SkillRecord, SkillRegistry
    from mas.library.skills.plugins.sk_tools import SkillToolsPlugin

    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text("---\nname: x\ndescription: y\n---\n", encoding="utf-8")
    reg = SkillRegistry()
    reg.register(SkillRecord(name="my-skill", description="y", path=skill_md))

    plugin = SkillToolsPlugin(registry=reg)
    tools = plugin.list_tools()
    activate = next(t for t in tools if t["name"] == "activate_skill")
    assert "my-skill" in activate["description"]


def test_list_tools_without_registry_generic_description():
    from mas.library.skills.plugins.sk_tools import SkillToolsPlugin

    plugin = SkillToolsPlugin()
    tools = plugin.list_tools()
    activate = next(t for t in tools if t["name"] == "activate_skill")
    assert activate["description"]  # non-empty generic description
