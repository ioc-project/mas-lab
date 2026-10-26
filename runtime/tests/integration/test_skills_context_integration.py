#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Skills context integration — fully in library-skills; no runtime skill imports."""


import pytest
from mas.library.skills.lib.resolver import resolve_skill_path
from mas.library.skills.lib.spec import skill_refs_from_manifest
from mas.library.skills.plugins.sk_catalog import SkillCatalogPlugin, attach_skill_catalog_plugin
from mas.runtime.boundary.context.assemble import assemble_llm_messages
from mas.runtime.boundary.context.chunk_meta import attach_chunk_meta
from mas.runtime.driver.mocks import AutoCtxAssembler
from mas.runtime.engine.tool_dispatch import format_tool_result


def test_skill_refs_from_manifest():
    manifest = {"spec": {"skills": ["skills/demo"]}}
    assert skill_refs_from_manifest(manifest) == ["skills/demo"]


def test_resolve_and_load_skill_md(tmp_path):
    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo skill.\n---\n# Demo skill\nDo the thing.",
        encoding="utf-8",
    )
    path = resolve_skill_path("skills/demo", base_dir=tmp_path)
    assert path is not None
    text = path.read_text(encoding="utf-8")
    assert "Demo skill" in text


def test_resolve_skill_exact_name_under_skills_dir(tmp_path):
    skill_dir = tmp_path / "skills" / "triage-protocol"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: triage-protocol\ndescription: Triage.\n---\n# Triage",
        encoding="utf-8",
    )
    path = resolve_skill_path("triage-protocol", base_dir=tmp_path)
    assert path is not None
    assert path.name == "SKILL.md"


def test_resolve_skill_normalizes_underscore_slug(tmp_path):
    skill_dir = tmp_path / "skills" / "triage-protocol"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: triage-protocol\ndescription: Triage.\n---\n# Triage",
        encoding="utf-8",
    )
    path = resolve_skill_path("triage_protocol", base_dir=tmp_path)
    assert path is not None
    assert path.name == "SKILL.md"


def test_catalog_plugin_missing_ref_raises(tmp_path):
    manifest = {"spec": {"skills": ["missing-skill"]}}
    with pytest.raises(ValueError, match="missing-skill"):
        SkillCatalogPlugin(manifest=manifest, base_dir=tmp_path)


def test_catalog_plugin_injects_catalog_only(tmp_path):
    app = tmp_path / "trip-planner"
    skill_dir = app / "skills" / "triage-protocol"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: triage-protocol\ndescription: Handles triage.\n---\n# Body content",
        encoding="utf-8",
    )
    manifest = {"spec": {"skills": ["triage-protocol"]}}
    plugin = SkillCatalogPlugin(manifest=manifest, base_dir=app)
    parts = plugin.collect_context()
    assert len(parts) == 1
    assert "triage-protocol" in parts[0].content
    assert "Handles triage" in parts[0].content
    assert "# Body content" not in parts[0].content


def test_catalog_plugin_uses_custom_skills_dir(tmp_path):
    skills_dir = tmp_path / "knowledge"
    skill_dir = skills_dir / "answer-formatting"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: answer-formatting\ndescription: Format answers.\n---\n# Body",
        encoding="utf-8",
    )
    plugin = SkillCatalogPlugin(
        manifest={"spec": {"skills": ["answer-formatting"]}},
        base_dir=skills_dir,
    )
    parts = plugin.collect_context()
    assert len(parts) == 1
    assert "answer-formatting" in parts[0].content


def test_attach_populates_ctx_skill_registry(tmp_path):
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: Does something.\n---\n# Body",
        encoding="utf-8",
    )

    class _Ctx:
        pass

    ctx = _Ctx()
    manifest = {"spec": {"skills": ["my-skill"]}}
    plugin = attach_skill_catalog_plugin(ctx, manifest, tmp_path)
    assert plugin is not None
    assert ctx.skill_registry is plugin.registry
    assert ctx.skill_registry.get("my-skill") is not None
    # plugin_collection has both catalog + activated-skills plugin
    assert hasattr(ctx, "plugin_collection")
    assert len(ctx.plugin_collection) == 2


def test_active_skill_is_reported_as_pinned_context_without_body_text(tmp_path):
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: Does something.\n---\n# Body\nSECRET_SKILL_TEXT",
        encoding="utf-8",
    )
    manifest = {"spec": {"skills": ["my-skill"], "context_manager": {"type": "stack"}}}
    ctx = AutoCtxAssembler(manifest=manifest)
    attach_skill_catalog_plugin(ctx, manifest, tmp_path)
    ctx.activated_skills_plugin.add_activated("my-skill", "# Body\nSECRET_SKILL_TEXT")

    assemble_llm_messages(
        ctx,
        manifest=manifest,
        resolved_model="vertex_ai/gemini-2.5-flash",
        context_window=1048576,
        completion_tokens=12000,
    )

    usage = ctx.last_context_usage
    pinned = [part for part in usage["context_parts"] if part["pinned"]]
    assert any(part["section_id"] == "skills/catalog" for part in pinned)
    assert any(part["section_id"] == "skills/activated/my-skill" for part in pinned)
    assert "SECRET_SKILL_TEXT" not in repr(usage)


@pytest.mark.parametrize("pin_activated", [True, False])
def test_activated_skill_body_survives_compaction_once(tmp_path, pin_activated):
    import json

    from mas.library.skills.plugins.sk_tools import SkillToolsPlugin

    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: Does something.\n---\n# Body\nSKILL_RULE_TEXT",
        encoding="utf-8",
    )
    manifest = {
        "spec": {
            "skills": ["my-skill"],
            "context_manager": {
                "type": "summarising",
                "params": {"keep_turns": 2, "summary_threshold": 200, "summarizer": "drop"},
            },
        }
    }
    ctx = AutoCtxAssembler(manifest=manifest)
    attach_skill_catalog_plugin(ctx, manifest, tmp_path, pin_activated=pin_activated)
    ctx.skill_pin_activated = pin_activated
    result = SkillToolsPlugin().on_execute_tool("activate_skill", {"name": "my-skill"}, ctx=ctx)
    text = format_tool_result(result, ctx=ctx, call_id="c1")
    tool_msg = attach_chunk_meta(
        {"role": "tool", "tool_call_id": "c1", "content": text},
        result.get("chunk") or {},
    )

    history = [
        {"role": "user", "content": "q0"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "activate_skill", "arguments": "{}"}}
            ],
        },
        tool_msg,
        {"role": "assistant", "content": "a0"},
    ]
    for i in range(1, 8):
        history += [
            {"role": "user", "content": f"q{i} " + "x" * 400},
            {"role": "assistant", "content": "y" * 400},
        ]
    ctx.committed_messages = history

    messages = assemble_llm_messages(ctx, manifest=manifest)

    assert not any(m.get("role") == "tool" for m in messages)  # activation turn was folded
    assert json.dumps(messages).count("SKILL_RULE_TEXT") == 1
