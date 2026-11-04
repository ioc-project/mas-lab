#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Google ADK skill plugin.

This is a REAL wrapper around google.adk.skills:
- Discovery uses ``google.adk.skills.load_skill_from_dir()`` which returns a
  ``Skill`` object (with validated frontmatter, in-memory resources).
- Metadata is read from ``Skill.frontmatter`` (not from python-frontmatter).
- Tier-2 body comes from ``Skill.instructions``.
- Tier-3 resources come from ``Skill.resources.scripts/references/assets``
  (all in-memory strings / Script objects, not file paths).
- Script execution writes the in-memory ``Script.src`` to a temp file and runs
  it, mirroring what ADK's ``SkillToolset.run_skill_script`` tool does.

Dependency::

    this library's ``adk`` extra
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .skill_plugin_base import (
    SkillActivation,
    SkillMetadata,
    SkillPlugin,
    guard_relative_name,
    run_script_from_source,
    split_resource_category,
)

logger = logging.getLogger(__name__)


class ADKSkillPlugin(SkillPlugin):
    """Skill implementation wrapping google.adk.skills.

    FRAMEWORK: Google Agent Development Kit (ADK) — Skills API (v1.25+)
    SOURCE: https://adk.dev/skills/
    API: google.adk.skills.load_skill_from_dir, Skill.frontmatter,
         Skill.instructions, Skill.resources
    """

    def __init__(
        self,
        base_dir: Path | None = None,
        working_dir: Path | None = None,
        run_dir: Path | None = None,
    ):
        try:
            from google.adk.skills import load_skill_from_dir as _check  # noqa: F401
        except ImportError as e:
            msg = "Google ADK not installed. Install via: uv pip install google-adk"
            raise ImportError(msg) from e

        super().__init__(working_dir=working_dir)
        self.base_dir = Path(base_dir or Path.cwd()).resolve()
        # name → Skill object (holds frontmatter + instructions + resources)
        self._skills: dict[str, Any] = {}
        # name → base directory (ADK doesn't expose path on Skill)
        self._skill_paths: dict[str, Path] = {}

    def discover(self, base_dir: Path) -> dict[str, SkillMetadata]:
        """Discover skills using ADK's load_skill_from_dir.

        ADK validates frontmatter (name kebab-case, description ≤1024 chars).
        Skills that fail ADK validation are silently skipped, matching ADK's
        own behaviour in SkillToolset.
        """
        from google.adk.skills import load_skill_from_dir as _loader
        return self._discover_with(base_dir, _loader)

    def _discover_with(self, base_dir: Path, loader: Any) -> dict[str, SkillMetadata]:
        """Internal discover, accepts loader function for testability."""
        self.base_dir = base_dir
        self._skills = {}
        self._skill_paths = {}

        for scan_subdir in ["skills", ".agents/skills"]:
            skill_dir = self.base_dir / scan_subdir
            if not skill_dir.exists():
                continue

            for subdir in sorted(skill_dir.iterdir()):
                if not subdir.is_dir():
                    continue
                skill_md = subdir / "SKILL.md"
                if not skill_md.exists():
                    continue

                try:
                    # ADK parses and validates frontmatter internally.
                    # Returns a Skill(frontmatter=…, instructions=…, resources=…).
                    skill = loader(subdir)
                except Exception as e:
                    logger.warning("ADK: skipping %s — %s", subdir.name, e)
                    continue

                if skill.name not in self._skills:
                    self._skills[skill.name] = skill
                    self._skill_paths[skill.name] = subdir

        return {
            name: SkillMetadata(
                name=skill.name,
                description=skill.description,  # Skill.description property
                path=self._skill_paths[name],
                license=skill.frontmatter.license,
                compatibility=skill.frontmatter.compatibility,
                allowed_tools=(
                    skill.frontmatter.allowed_tools.split()
                    if skill.frontmatter.allowed_tools
                    else None
                ),
                # Expose the raw frontmatter for introspection
                metadata_dict=skill.frontmatter.metadata,
            )
            for name, skill in self._skills.items()
        }

    def activate(self, skill_name: str) -> SkillActivation:
        """Return Tier-2 skill instructions from Skill.instructions.

        The body is the text ADK already parsed from the SKILL.md body section;
        we do NOT re-read or re-parse the file here.
        """
        skill = self._skills.get(skill_name)
        if not skill:
            msg = f"Skill not found: {skill_name}"
            raise ValueError(msg)

        # Build resource listing with real filesystem paths.
        # ADK loaded resources from disk into memory — the source files still
        # exist at _skill_paths[skill_name]/<category>/<name>.  Reconstruct
        # the real paths so callers get navigable Path objects, not sentinels.
        skill_dir = self._skill_paths[skill_name]
        resources: dict[str, Path] = {}
        for ref in skill.resources.list_references():
            resources[f"references/{ref}"] = skill_dir / "references" / ref
        for asset in skill.resources.list_assets():
            resources[f"assets/{asset}"] = skill_dir / "assets" / asset
        for script in skill.resources.list_scripts():
            resources[f"scripts/{script}"] = skill_dir / "scripts" / script

        return SkillActivation(
            name=skill_name,
            body=skill.instructions,
            resources=resources,
        )

    def read_resource(self, skill_name: str, resource_path: str) -> str:
        """Return resource content from Skill.resources.

        resource_path must be 'category/name', e.g. 'references/doc.md'.
        Content is retrieved from the in-memory ADK Resources object.
        """
        skill = self._skills.get(skill_name)
        if not skill:
            msg = f"Skill not found: {skill_name}"
            raise ValueError(msg)

        category, name = split_resource_category(resource_path)

        if category == "references":
            content = skill.resources.get_reference(name)
        elif category == "assets":
            content = skill.resources.get_asset(name)
        else:  # scripts
            script_obj = skill.resources.get_script(name)
            content = script_obj.src if script_obj is not None else None

        if content is None:
            msg = f"Resource not found: {resource_path}"
            raise FileNotFoundError(msg)

        return content.decode("utf-8") if isinstance(content, bytes) else content

    def run_script(
        self,
        skill_name: str,
        script_name: str,
        args: list[str] | None = None,
        timeout: int = 30,
        env_extra: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Execute a skill script.

        ADK stores script content in memory as ``Script.src``.  We delegate to
        ``run_script_from_source`` which handles temp-file creation, interpreter
        selection, and environment merging — mirroring what ADK's
        ``SkillToolset.run_skill_script`` tool does internally.
        """
        skill = self._skills.get(skill_name)
        if not skill:
            msg = f"Skill not found: {skill_name}"
            raise ValueError(msg)

        guard_relative_name(script_name)

        script_obj = skill.resources.get_script(script_name)
        if script_obj is None:
            msg = f"Script not found in ADK resources: {script_name}"
            raise FileNotFoundError(msg)

        return run_script_from_source(
            source=str(script_obj),  # Script.__str__ returns .src
            script_name=script_name,
            args=args,
            timeout=timeout,
            env_extra=env_extra,
            cwd=self.get_working_dir(),  # session-scoped; state files persist across calls
        )

    def allowed_tools(self, skill_name: str) -> list[str]:
        """Return allowed_tools from Skill.frontmatter (ADK Frontmatter model)."""
        skill = self._skills.get(skill_name)
        if not skill or not skill.frontmatter.allowed_tools:
            return []
        return skill.frontmatter.allowed_tools.split()

