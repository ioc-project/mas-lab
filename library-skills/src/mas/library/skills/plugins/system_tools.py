#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Inject skill-access / run-skill-script as system tools.

Default: nothing from the skills system is advertised to the LLM.

Implicit:
  - ``spec.skills`` with at least one entry → ``activate_skill`` (and list/read)
  - at least one of those skills has a ``scripts/`` file → ``run_skill_script``

Explicit (manifest):
  - ``spec.system_plugins: [activate_skill]``
  - ``spec.system_plugins: [run_skill_script]``
  - ``spec.context_sources: [{native: {auto_inject: true}}]`` (scripts)
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mas.library.skills.lib.resolver import resolve_skill_path
from mas.library.skills.plugins.sk_shell import RunSkillScriptPlugin
from mas.library.skills.plugins.sk_tools import SkillToolsPlugin
from mas.runtime.system_plugins import InstanceSystemPlugin, SessionContext

SKILL_ACCESS_REFS = frozenset(
    {
        "skills:tools/skill-access.tool.yaml",
        "pkg://skills/tools/skill-access.tool.yaml",
    }
)
SKILL_SHELL_REFS = frozenset(
    {
        "skills:tools/run-skill-script.tool.yaml",
        "pkg://skills/tools/run-skill-script.tool.yaml",
    }
)


def skill_refs(skills_spec: Any) -> list[str]:
    refs: list[str] = []
    if not isinstance(skills_spec, list):
        return refs
    for item in skills_spec:
        if isinstance(item, str) and item.strip():
            refs.append(item.strip())
    return refs


def _tools_have_ref(tools_spec: list[Any] | None, refs: frozenset[str]) -> bool:
    for item in tools_spec or []:
        if isinstance(item, dict) and str(item.get("ref") or "").strip() in refs:
            return True
    return False


def any_declared_skill_has_scripts(refs: list[str], *, base_dir: Path) -> bool:
    """True when at least one listed skill has a non-empty ``scripts/`` directory."""
    for ref in refs:
        skill_md = resolve_skill_path(ref, base_dir=base_dir)
        if skill_md is None:
            continue
        scripts = skill_md.parent / "scripts"
        if scripts.is_dir() and any(p.is_file() for p in scripts.iterdir()):
            return True
    return False


def should_inject_activate_skill(
    *,
    skills_spec: Any,
    tools_spec: list[Any] | None,
) -> bool:
    """Advertise activate_skill when skills are listed or explicitly opted in.

    A YAML ``skill-access.tool.yaml`` ref is the older explicit form; skip
    system inject in that case so the plugin is not loaded twice.
    """
    if _tools_have_ref(tools_spec, SKILL_ACCESS_REFS):
        return False
    return bool(skill_refs(skills_spec))


def should_inject_run_skill_script(
    *,
    skills_spec: Any,
    tools_spec: list[Any] | None,
    base_dir: Path,
    auto_inject_scripts: bool = False,
) -> bool:
    if _tools_have_ref(tools_spec, SKILL_SHELL_REFS):
        return False
    if auto_inject_scripts:
        return True
    refs = skill_refs(skills_spec)
    if not refs:
        return False
    return any_declared_skill_has_scripts(refs, base_dir=base_dir)


class ActivateSkillPlugin(InstanceSystemPlugin):
    """System plugin wrapping ``SkillToolsPlugin`` (activate/list/read). Granted by ``spec.skills``."""

    name = "activate_skill"

    @classmethod
    def should_load(cls, spec: Mapping[str, Any], settings: Mapping[str, Any], *, session: SessionContext) -> bool:
        return should_inject_activate_skill(skills_spec=spec.get("skills"), tools_spec=spec.get("tools"))

    def make_instance(self, session: SessionContext | None) -> SkillToolsPlugin:
        return SkillToolsPlugin()


class RunSkillScriptSystemPlugin(InstanceSystemPlugin):
    """System plugin wrapping ``RunSkillScriptPlugin``. Granted when a listed skill ships ``scripts/``."""

    name = "run_skill_script"

    @classmethod
    def should_load(cls, spec: Mapping[str, Any], settings: Mapping[str, Any], *, session: SessionContext) -> bool:
        return should_inject_run_skill_script(
            skills_spec=spec.get("skills"),
            tools_spec=spec.get("tools"),
            base_dir=Path(session.skills_dir) if session.skills_dir else Path("."),
            auto_inject_scripts=session.auto_inject_scripts,
        )

    def make_instance(self, session: SessionContext | None) -> RunSkillScriptPlugin:
        return RunSkillScriptPlugin()
