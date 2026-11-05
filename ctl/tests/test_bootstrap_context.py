#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Bootstrap context injection — spec.context.* and skill resolution."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.ctl.session.bootstrap import _apply_manifest_context
from mas.library.skills.lib.resolver import resolve_skill_path
from mas.library.skills.plugins.sk_catalog import SkillCatalogPlugin
from mas.runtime.boundary.context.manifest_context import (
    ContextRefNotFoundError,
    resolve_context_chunk,
)
from mas.runtime.driver.mocks import AutoCtxAssembler


def test_apply_manifest_context_reads_context_role():
    ctx = AutoCtxAssembler()
    manifest = {
        "spec": {
            "context": {
                "role": "You are the trip planner. Delegate before acting.",
                "tool_usage": "Use tools carefully.",
            }
        }
    }
    _apply_manifest_context(ctx, manifest, Path("/tmp/agents"))
    assert any("[role]" in chunk and "trip planner" in chunk for chunk in ctx.injected_context)
    assert any("[tool_usage]" in chunk for chunk in ctx.injected_context)


def test_apply_manifest_context_reads_context_ref(tmp_path: Path):
    role_file = tmp_path / "role.md"
    role_file.write_text("External role body.", encoding="utf-8")
    ctx = AutoCtxAssembler()
    manifest = {"spec": {"context": {"role": {"ref": "role.md"}}}}
    _apply_manifest_context(ctx, manifest, tmp_path)
    assert ctx.injected_context == ["[role] External role body."]


def test_apply_manifest_context_reads_context_path_string(tmp_path: Path):
    role_file = tmp_path / "prompts" / "role.md"
    role_file.parent.mkdir()
    role_file.write_text("Path string role.", encoding="utf-8")
    ctx = AutoCtxAssembler()
    manifest = {"spec": {"context": {"role": "./prompts/role.md"}}}
    _apply_manifest_context(ctx, manifest, tmp_path)
    assert ctx.injected_context == ["[role] Path string role."]


def test_apply_manifest_context_reads_array_chunk_as_one_joined_line(tmp_path: Path):
    escalation = tmp_path / "escalation.md"
    escalation.write_text("Confirm the budget before booking.", encoding="utf-8")
    ctx = AutoCtxAssembler()
    manifest = {
        "spec": {
            "context": {
                "role": ["You are a travel planner.", {"ref": "escalation.md"}],
            }
        }
    }
    _apply_manifest_context(ctx, manifest, tmp_path)
    assert ctx.injected_context == ["[role] You are a travel planner.\nConfirm the budget before booking."]


def test_apply_manifest_context_only_reads_context_chunks():
    ctx = AutoCtxAssembler()
    manifest = {
        "spec": {
            "role": {"instructions": "removed"},
            "intent": "removed",
            "system_prompt": "removed",
        }
    }
    _apply_manifest_context(ctx, manifest, Path("/tmp"))
    assert ctx.injected_context == []


def test_resolve_context_chunk_ref_object(tmp_path: Path):
    path = tmp_path / "chunk.md"
    path.write_text("chunk text", encoding="utf-8")
    assert resolve_context_chunk({"ref": "chunk.md"}, base_dir=tmp_path) == "chunk text"


def test_resolve_context_chunk_missing_ref_raises(tmp_path: Path):
    with pytest.raises(ContextRefNotFoundError, match="missing.md"):
        resolve_context_chunk({"ref": "missing.md"}, base_dir=tmp_path)


def test_resolve_skill_path_finds_skills_subdir_with_hyphen_slug(tmp_path: Path):
    skill_dir = tmp_path / "skills" / "triage-protocol"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: triage-protocol\ndescription: Handles triage requests.\n---\n# Triage protocol",
        encoding="utf-8",
    )

    path = resolve_skill_path("triage_protocol", base_dir=tmp_path)
    assert path is not None

    # Verify catalog plugin emits the catalog (not full text)
    manifest = {"spec": {"skills": ["triage_protocol"]}}
    plugin = SkillCatalogPlugin(manifest=manifest, base_dir=tmp_path)
    parts = plugin.collect_context()
    assert len(parts) == 1
    assert "triage-protocol" in parts[0].content
    assert "Handles triage" in parts[0].content


def test_inject_skills_uses_app_root_not_agent_dir(tmp_path: Path):
    app_root = tmp_path / "trip-planner"
    agents_dir = app_root / "agents"
    agents_dir.mkdir(parents=True)
    skill_dir = app_root / "skills" / "data-access-protocol"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: data-access-protocol\ndescription: Data access guidance.\n---\n# Data access",
        encoding="utf-8",
    )

    manifest = {"spec": {"skills": ["data_access_protocol"]}}

    # Skills in parent skills/ dir are found even from agents/ subdirectory
    # (parent-walk resolution) — verified via SkillCatalogPlugin directly.
    plugin_from_agents = SkillCatalogPlugin(manifest=manifest, base_dir=agents_dir)
    parts_agents = plugin_from_agents.collect_context()
    assert len(parts_agents) == 1
    assert "data-access-protocol" in parts_agents[0].content

    plugin_from_root = SkillCatalogPlugin(manifest=manifest, base_dir=app_root)
    parts_root = plugin_from_root.collect_context()
    assert len(parts_root) == 1
    assert "data-access-protocol" in parts_root[0].content


def test_resolve_skill_plugin_config_prefers_manifest_entry_over_env(monkeypatch, tmp_path: Path):
    from mas.ctl.session.bootstrap import _resolve_skill_plugin_config

    monkeypatch.setenv("MAS_SKILL_IMPL", "native")
    manifest = {
        "spec": {
            "tools": [
                {
                    "ref": "skills:tools/skill-access.tool.yaml",
                    "params": {"impl": "adk", "base_dir": "./skills"},
                }
            ]
        }
    }
    cfg = _resolve_skill_plugin_config(manifest, default_base_dir=tmp_path)
    assert cfg.impl == "adk"
    assert cfg.base_dir == (tmp_path / "skills").resolve()


def test_auto_inject_skill_tools_is_noop():
    """YAML mutation is gone; skill tools are system tools at provider load."""
    from mas.ctl.session.bootstrap import _auto_inject_skill_tools

    manifest = {"spec": {"skills": ["answer-formatting"]}}
    _auto_inject_skill_tools(manifest)
    assert "tools" not in manifest["spec"]


def test_auto_inject_skill_tools_noop_without_skills():
    from mas.ctl.session.bootstrap import _auto_inject_skill_tools

    manifest = {"spec": {}}
    _auto_inject_skill_tools(manifest)
    assert "tools" not in manifest["spec"]


def test_resolve_skill_plugin_config_reads_auto_inject_from_context_sources(tmp_path: Path):
    from mas.ctl.session.bootstrap import _resolve_skill_plugin_config

    manifest = {"spec": {"context_sources": [{"native": {"auto_inject": True}}]}}
    cfg = _resolve_skill_plugin_config(manifest, default_base_dir=tmp_path)
    assert cfg.auto_inject_scripts is True


def test_resolve_skill_plugin_config_auto_inject_defaults_false(tmp_path: Path):
    from mas.ctl.session.bootstrap import _resolve_skill_plugin_config

    cfg = _resolve_skill_plugin_config({"spec": {}}, default_base_dir=tmp_path)
    assert cfg.auto_inject_scripts is False
    assert cfg.allow_unload is False


def test_resolve_skill_plugin_config_reads_pin_and_unload(tmp_path: Path):
    from mas.ctl.session.bootstrap import _resolve_skill_plugin_config

    manifest = {"spec": {"context_sources": [{"native": {"pin_activated": False, "allow_unload": True}}]}}
    cfg = _resolve_skill_plugin_config(manifest, default_base_dir=tmp_path)
    assert cfg.pin_activated is False
    assert cfg.allow_unload is True


def test_interface_contracts_resolve_from_full_agent_manifest_and_keep_overrides():
    from mas.ctl.manifest.spec_bindings import validate_agent_spec_bindings
    from mas.ctl.session.bootstrap import _resolve_interface_contract
    from mas.library.standard.plugins.hitl.contracts import (
        RegistryHitlContract,
        RegistryUserIOContract,
    )

    manifest = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "contract-test"},
        "spec": {
            "description": "contract test",
            "hitl_contract": {"type": "registry"},
            "user_io_contract": "registry",
        },
    }
    validate_agent_spec_bindings(manifest["spec"])

    hitl_contract = _resolve_interface_contract(None, manifest, "hitl_contract")
    user_io_contract = _resolve_interface_contract(None, manifest, "user_io_contract")
    override = object()

    assert isinstance(hitl_contract, RegistryHitlContract)
    assert isinstance(user_io_contract, RegistryUserIOContract)
    assert _resolve_interface_contract(override, manifest, "hitl_contract") is override
