#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""LangChain/LangGraph skill plugin via deepagents.

This is a REAL wrapper around deepagents (LangGraph agent harness):

- **Discovery**: ``deepagents.middleware.skills._list_skills(backend, path)``
  uses deepagents' own frontmatter parser (validates name, description, etc.)
  and returns ``SkillMetadata`` TypedDicts.
- **Backend**: ``deepagents.backends.FilesystemBackend`` — the same backend
  deepagents uses at runtime to serve files to the agent.
- **Activation (L2)**: ``backend.read(skill["path"])`` → ``ReadResult`` →
  ``file_data["content"]`` — file read goes through the deepagents backend,
  not a direct filesystem call.
- **Resources (L3)**: ``backend.glob(pattern)`` lists resources; each resource
  is read via ``backend.read(path)``.
- **Execution**: Script source is read through the backend, written to a temp
  file, and executed via subprocess.  deepagents' ``LangSmithSandbox`` is an
  opt-in alternative for containerised execution.

Architecture notes:
- deepagents is built on LangGraph (``langgraph.checkpoint``, ``.store``,
  ``.runtime``).  Its ``create_deep_agent()`` returns a ``CompiledStateGraph``.
- ``SkillsMiddleware`` handles discovery/activation inside the deep agent loop;
  here we expose the same discovery/read primitives through our ``SkillPlugin``
  interface so any MAS Lab agent can use skills without embedding a full deep
  agent graph.

Dependency::

    this library's ``langchain`` extra
    # installs: deepagents (which pulls langgraph, langchain-core, etc.)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import frontmatter
from mas._dist import extra_hint

from .skill_plugin_base import (
    SkillActivation,
    SkillMetadata,
    SkillPlugin,
    guard_relative_name,
    run_script_from_source,
    split_resource_category,
)

logger = logging.getLogger(__name__)

# Virtual path prefixes deepagents uses when scanning default skill locations.
_SKILL_PATHS = ["/skills/", "/.agents/skills/"]


def _backend_read(backend: Any, path: str) -> str:
    """Read file content via deepagents backend; raise FileNotFoundError on error."""
    result = backend.read(path)
    if result.error:
        msg = f"deepagents backend read error for {path!r}: {result.error}"
        raise FileNotFoundError(msg)
    fd = result.file_data
    if fd is None:
        msg = f"deepagents backend returned no data for {path!r}"
        raise FileNotFoundError(msg)
    return fd["content"]  # always utf-8 str for text files


def _extract_body(skill_md_content: str) -> str:
    """Strip YAML frontmatter and return the Markdown body."""
    post = frontmatter.loads(skill_md_content)
    return post.content


class LangChainSkillPlugin(SkillPlugin):
    """Skill implementation wrapping deepagents (LangGraph).

    FRAMEWORK: deepagents — LangGraph agent harness by LangChain
    PACKAGE:   deepagents>=0.7.0  (https://pypi.org/project/deepagents/)
    API USED:
      - deepagents.middleware.skills._list_skills  (discovery + frontmatter parsing)
      - deepagents.backends.FilesystemBackend      (read/glob via backend)
    """

    def __init__(
        self,
        base_dir: Path | None = None,
        working_dir: Path | None = None,
        run_dir: Path | None = None,
    ):
        try:
            from deepagents.backends import FilesystemBackend  # noqa: F401
            from deepagents.middleware.skills import _list_skills  # noqa: F401
        except ImportError as e:
            msg = (
                "deepagents not installed. "
                f"Install via: {extra_hint('langchain', file=__file__)}"
            )
            raise ImportError(msg) from e

        super().__init__(working_dir=working_dir, run_dir=run_dir)
        self.base_dir = Path(base_dir or Path.cwd()).resolve()
        # FilesystemBackend serves virtual paths /… relative to base_dir
        self._backend: Any = None
        # deepagents SkillMetadata TypedDicts, keyed by skill name
        self._skill_meta: dict[str, Any] = {}
        # Our SkillMetadata objects exposed via the SkillPlugin interface
        self._metadata_cache: dict[str, SkillMetadata] | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_backend(self) -> Any:
        if self._backend is None:
            from deepagents.backends import FilesystemBackend
            self._backend = FilesystemBackend(root_dir=str(self.base_dir))
        return self._backend

    def _skill_dir(self, skill_name: str) -> str:
        """Return the virtual directory path, e.g. '/skills/name/'."""
        return self._skill_meta[skill_name]["path"].replace("SKILL.md", "")

    def _list_resources(self, skill_name: str) -> dict[str, Path]:
        """List L3 resource files via deepagents backend.glob().

        Returns real filesystem ``Path`` objects derived from the deepagents
        virtual path.  This is correct for ``FilesystemBackend``, where virtual
        paths map 1:1 to real disk paths under ``base_dir``.

        If you swap to a non-filesystem backend (``StateBackend``,
        ``StoreBackend``), the returned ``Path`` objects will not map to real
        files — use ``read_resource()`` to access content in that case.
        """
        backend = self._get_backend()
        skill_dir = self._skill_dir(skill_name)
        resources: dict[str, Path] = {}

        try:
            glob_result = backend.glob(f"{skill_dir}**/*")
        except Exception as e:
            logger.debug("deepagents glob failed for %s: %s", skill_dir, e)
            return resources

        if glob_result.error or not glob_result.matches:
            return resources

        for match in glob_result.matches:
            vpath = match.get("path")
            if not vpath:
                logger.warning("deepagents glob match missing 'path' key: %r", match)
                continue
            if match.get("is_dir"):
                continue
            if vpath.endswith("SKILL.md"):
                continue
            # rel path from skill root: "scripts/main.py"
            rel = vpath[len(skill_dir):]
            resources[rel] = self.base_dir / vpath.lstrip("/")

        return resources

    # ------------------------------------------------------------------
    # SkillPlugin interface
    # ------------------------------------------------------------------

    def discover(self, base_dir: Path) -> dict[str, SkillMetadata]:
        """Discover skills using deepagents._list_skills() + FilesystemBackend.

        deepagents validates SKILL.md frontmatter (name kebab-case,
        description ≤1024 chars) and returns typed ``SkillMetadata`` dicts.
        Skills that fail deepagents validation are silently skipped.
        First source wins when the same name appears in multiple paths.
        """
        from deepagents.middleware.skills import _list_skills

        self.base_dir = base_dir
        self._backend = None  # reset so new base_dir is picked up
        backend = self._get_backend()
        self._skill_meta = {}
        self._metadata_cache = {}

        for source_path in _SKILL_PATHS:
            try:
                skills = _list_skills(backend, source_path)
            except Exception as e:
                logger.debug("deepagents _list_skills(%s) failed: %s", source_path, e)
                continue

            for skill in skills:
                name: str = skill["name"]
                if name in self._skill_meta:
                    continue  # first source wins

                self._skill_meta[name] = skill
                # deepagents already parses allowed-tools → list[str]
                self._metadata_cache[name] = SkillMetadata(
                    name=skill["name"],
                    description=skill["description"],
                    path=base_dir / skill["path"].lstrip("/").replace("/SKILL.md", ""),
                    license=skill.get("license"),
                    compatibility=skill.get("compatibility"),
                    allowed_tools=skill.get("allowed_tools") or None,
                    metadata_dict=skill.get("metadata"),
                )

        return self._metadata_cache

    def activate(self, skill_name: str) -> SkillActivation:
        """Return Tier-2 instructions read via deepagents FilesystemBackend.

        File access goes through the backend — this works identically with any
        other deepagents backend (StateBackend, StoreBackend, GCS) once plugged
        in.
        """
        if self._metadata_cache is None:
            self.discover(self.base_dir)

        if skill_name not in self._skill_meta:
            msg = f"Skill not found: {skill_name}"
            raise ValueError(msg)

        backend = self._get_backend()
        skill = self._skill_meta[skill_name]
        raw = _backend_read(backend, skill["path"])
        body = _extract_body(raw)

        resources = self._list_resources(skill_name)
        return SkillActivation(name=skill_name, body=body, resources=resources)

    def read_resource(self, skill_name: str, resource_path: str) -> str:
        """Read a skill resource through the deepagents FilesystemBackend.

        Path traversal guard: ``resource_path`` must start with a known
        resource category (``scripts/``, ``references/``, ``assets/``).
        """
        if self._metadata_cache is None:
            self.discover(self.base_dir)

        if skill_name not in self._skill_meta:
            msg = f"Skill not found: {skill_name}"
            raise ValueError(msg)

        split_resource_category(resource_path)

        backend = self._get_backend()
        vpath = f"{self._skill_dir(skill_name)}{resource_path}"
        return _backend_read(backend, vpath)

    def run_script(
        self,
        skill_name: str,
        script_name: str,
        args: list[str] | None = None,
        timeout: int = 30,
        env_extra: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Execute a skill script.

        Script source is read through the deepagents backend, then delegated to
        ``run_script_from_source`` which handles temp-file creation, interpreter
        selection, and environment merging.  For containerised execution, swap in
        ``deepagents.backends.langsmith.LangSmithSandbox``.
        """
        if self._metadata_cache is None:
            self.discover(self.base_dir)

        if skill_name not in self._skill_meta:
            msg = f"Skill not found: {skill_name}"
            raise ValueError(msg)

        guard_relative_name(script_name)

        backend = self._get_backend()
        vpath = f"{self._skill_dir(skill_name)}scripts/{script_name}"

        try:
            source = _backend_read(backend, vpath)
        except FileNotFoundError:
            msg = f"Script not found: {script_name}"
            raise FileNotFoundError(msg) from None

        return run_script_from_source(
            source=source,
            script_name=script_name,
            args=args,
            timeout=timeout,
            env_extra=env_extra,
            cwd=self.get_working_dir(),  # session-scoped; state files persist across calls
        )

    def allowed_tools(self, skill_name: str) -> list[str]:
        """Return allowed-tools from deepagents SkillMetadata.

        deepagents already parses the space-delimited ``allowed-tools`` field
        into a ``list[str]`` — no extra splitting needed here.
        """
        if self._metadata_cache is None:
            self.discover(self.base_dir)
        skill = self._skill_meta.get(skill_name)
        return skill.get("allowed_tools") or [] if skill else []
