#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Skill system-tool injection: implicit from spec.skills / scripts, explicit in manifest."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from mas.library.skills.plugins.system_tools import (
    should_inject_activate_skill,
    should_inject_run_skill_script,
)


def _skill(tmp_path: Path, name: str, *, scripts: bool = False) -> None:
    skill_dir = tmp_path / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Use when testing. Call activate_skill.\n---\n# {name}\n",
        encoding="utf-8",
    )
    if scripts:
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "run.py").write_text("print('ok')\n", encoding="utf-8")


def test_activate_skill_not_injected_without_skills() -> None:
    assert should_inject_activate_skill(skills_spec=[], tools_spec=[]) is False
    assert should_inject_activate_skill(skills_spec=None, tools_spec=None) is False


def test_activate_skill_implicit_when_skills_listed() -> None:
    assert should_inject_activate_skill(skills_spec=["answer-formatting"], tools_spec=[]) is True


def test_activate_skill_skipped_when_yaml_ref_present() -> None:
    tools = [{"ref": "skills:tools/skill-access.tool.yaml"}]
    assert should_inject_activate_skill(skills_spec=["answer-formatting"], tools_spec=tools) is False


def test_run_skill_script_not_injected_without_scripts(tmp_path: Path) -> None:
    _skill(tmp_path, "plain")
    assert (
        should_inject_run_skill_script(
            skills_spec=["plain"],
            tools_spec=[],
            base_dir=tmp_path,
        )
        is False
    )


def test_run_skill_script_implicit_when_skill_has_scripts(tmp_path: Path) -> None:
    _skill(tmp_path, "with-scripts", scripts=True)
    assert (
        should_inject_run_skill_script(
            skills_spec=["with-scripts"],
            tools_spec=[],
            base_dir=tmp_path,
        )
        is True
    )


def test_run_skill_script_auto_inject_flag(tmp_path: Path) -> None:
    _skill(tmp_path, "plain")
    assert (
        should_inject_run_skill_script(
            skills_spec=["plain"],
            tools_spec=[],
            base_dir=tmp_path,
            auto_inject_scripts=True,
        )
        is True
    )


def _advertised(tools_spec: list, skills: list[str], base: Path) -> set[str]:
    from mas.library.skills.lib.registry import SkillRecord, SkillRegistry
    from mas.runtime.engine.manifest_tool_provider import build_manifest_tool_provider

    provider = build_manifest_tool_provider(tools_spec, base, skills_spec=skills, skills_dir=base)
    registry = SkillRegistry()
    for name in skills:
        registry.register(SkillRecord(name=name, description="d", path=base / "skills" / name / "SKILL.md"))
    ctx = SimpleNamespace(skill_registry=registry)
    return {t["name"] for t in provider.list_tools(ctx=ctx)}


def test_provider_exposes_activate_skill_without_host_opt_in(tmp_path: Path) -> None:
    """Regression: skill tools were gated behind the user-IO system-tool opt-in."""
    _skill(tmp_path, "plain")
    names = _advertised([], ["plain"], tmp_path)
    assert "activate_skill" in names
    assert "run_skill_script" not in names
    assert "request_human_input" not in names


def test_provider_exposes_run_skill_script_when_skill_ships_scripts(tmp_path: Path) -> None:
    _skill(tmp_path, "with-scripts", scripts=True)
    assert {"activate_skill", "run_skill_script"} <= _advertised([], ["with-scripts"], tmp_path)


def test_provider_exposes_nothing_from_skills_without_skills(tmp_path: Path) -> None:
    assert _advertised([], [], tmp_path) == set()
