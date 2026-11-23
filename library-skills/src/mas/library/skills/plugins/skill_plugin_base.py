#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Base abstraction for skill implementations across frameworks.

Allows MAS Lab to support multiple skill backends:
- plugin-skills (native custom implementation)
- plugin-skills-langchain (deepagents / LangGraph)
- plugin-skills-adk (Google ADK)

All implementations conform to SkillPlugin interface for easy swapping.
"""

from __future__ import annotations

import atexit
import logging
import shutil
import subprocess
import sys
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Environment variable names a caller-supplied `env_extra` override must never
# touch — these control interpreter/dynamic-linker behavior and can subvert
# sandboxing (e.g. LD_PRELOAD to inject a malicious shared library, PATH/
# PYTHONPATH to substitute a trusted binary/module with an attacker-controlled
# one). Shared by run_script_from_source() (ADK/LangChain backends) and
# sk_shell.py's RunSkillScriptPlugin (native backend) — a single denylist
# enforced at every subprocess-execution entry point, not just the tool-call
# boundary, so direct SkillPlugin usage is defended too (defense in depth).
ENV_DENYLIST = frozenset(
    {
        "PATH",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONHOME",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
    }
)


def sanitize_extra_env(raw: dict[str, Any] | None) -> dict[str, str]:
    """Filter caller-supplied env overrides, dropping denylisted/invalid keys."""
    sanitized: dict[str, str] = {}
    for key, value in (raw or {}).items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        if key.upper() in ENV_DENYLIST:
            logger.warning("skill script execution: rejected denylisted env override %r", key)
            continue
        sanitized[key] = value
    return sanitized


def require_str_arg(arguments: dict[str, Any], key: str) -> str:
    """Return the string value of ``arguments[key]``, or ``""`` if absent.

    Raises ``TypeError`` if present but not a string — callers must not
    silently coerce non-string tool-call arguments (e.g. a list or int
    passed where a name/path is expected) via ``str(...)``, which would mask
    a malformed call instead of reporting a clear error.
    """
    value = arguments.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TypeError(f"{key!r} must be a string, got {type(value).__name__}")
    return value


def require_str_list_arg(arguments: dict[str, Any], key: str) -> list[str]:
    """Return the list-of-strings value of ``arguments[key]``, or ``[]`` if absent.

    Raises ``TypeError`` if present but not a list, or if any element isn't a
    string. Without this check, a malformed call (e.g. ``args: [1, 2]``,
    bypassing JSON-schema validation) reaches ``subprocess.run()`` deep
    inside the sandbox runner, which raises an *uncaught* ``TypeError`` —
    there is no ``try/except`` around the native execution path in
    ``sk_shell.py``, so that exception would propagate out of the tool call
    instead of returning a clean ``{"error": ...}`` result.
    """
    value = arguments.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"{key!r} must be a list, got {type(value).__name__}")
    for item in value:
        if not isinstance(item, str):
            raise TypeError(f"{key!r} items must all be strings, got {type(item).__name__}")
    return value


def require_dict_arg(arguments: dict[str, Any], key: str) -> dict[str, Any]:
    """Return the dict value of ``arguments[key]``, or ``{}`` if absent.

    Raises ``TypeError`` if present but not a dict — e.g. ``dict("oops")``
    would otherwise raise an uncaught ``ValueError``/``TypeError`` of its
    own, deep inside a call the caller doesn't expect to fail this way.
    """
    value = arguments.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError(f"{key!r} must be a dict, got {type(value).__name__}")
    return value


def require_number_arg(arguments: dict[str, Any], key: str, default: int) -> int:
    """Return ``int(arguments[key])``, or ``default`` if absent.

    Raises ``TypeError`` if present but not a number — ``int("abc")`` would
    otherwise raise an uncaught ``ValueError`` from a malformed tool call.
    """
    value = arguments.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{key!r} must be a number, got {type(value).__name__}")
    return int(value)


# Resource-path traversal guards
# -------------------------------
# All three backends bundle skill resources under one of these three
# category directories (``scripts/``, ``references/``, ``assets/``); every
# ``read_resource()``/``run_script()`` implementation must reject anything
# else before touching the filesystem/backend. Two guard *shapes* exist:
#   - ADK resolves resources through a pure in-memory dict keyed by
#     ``"category/name"`` strings (no real filesystem path to traverse) ->
#     validated by ``split_resource_category()`` (category check) plus
#     ``guard_relative_name()`` (defense in depth, even though a ``..`` in
#     the name just misses the dict key here).
#   - LangChain's ``FilesystemBackend`` and Native both resolve through a
#     REAL filesystem rooted at ``base_dir`` (LangChain: all skills share
#     one root, not just the current skill's directory) -> a ``name``
#     containing ``..`` can escape the intended skill/category directory.
#     LangChain uses ``split_resource_category()`` + ``guard_relative_name()``
#     (string-level ``..``/absolute-path rejection, since the backend is
#     opaque); Native additionally uses ``guard_resource_realpath()``, which
#     resolves the real path and also catches symlink-based escapes.
RESOURCE_CATEGORIES = frozenset({"references", "assets", "scripts"})


def guard_relative_name(name: str) -> str:
    """Reject a resource/script ``name`` that could escape its directory.

    Allows nested subdirectories (e.g. ``"sub/doc.md"``) but rejects ``..``
    components and absolute paths. Intended for backends where the name is
    concatenated into a virtual or real path string rather than resolved
    via ``Path.resolve()`` + ``relative_to()`` (see ``guard_resource_realpath``
    for the native, real-filesystem equivalent).
    """
    if not name or Path(name).is_absolute() or ".." in Path(name).parts:
        msg = f"Path traversal attempt: {name!r}"
        raise ValueError(msg)
    return name


def split_resource_category(resource_path: str) -> tuple[str, str]:
    """Split ``"category/name"`` into ``(category, name)``.

    Raises ``ValueError`` if the category isn't one of the known resource
    buckets, or if ``name`` attempts to traverse outside its directory (see
    ``guard_relative_name()``).
    """
    parts = resource_path.split("/", 1)
    if len(parts) != 2 or parts[0] not in RESOURCE_CATEGORIES:
        msg = f"Resource path must be 'category/name' (references|assets|scripts): {resource_path!r}"
        raise ValueError(msg)
    category, name = parts
    guard_relative_name(name)
    return category, name


def guard_resource_realpath(skill_dir: Path, resource_path: str) -> Path:
    """Resolve ``resource_path`` under ``skill_dir`` and reject escapes.

    Used by the native backend, which reads real files from disk. Returns
    the resolved absolute path; raises ``ValueError`` on traversal attempts
    (``../``, absolute paths, or symlinks that resolve outside ``skill_dir``).
    """
    resolved_skill_dir = skill_dir.resolve()
    file_path = (skill_dir / resource_path).resolve()
    try:
        file_path.relative_to(resolved_skill_dir)
    except ValueError:
        msg = f"Path traversal attempt: {resource_path}"
        raise ValueError(msg) from None
    return file_path


@dataclass
class SkillMetadata:
    """Skill metadata (Tier 1: discovery)."""

    name: str
    description: str
    path: Path
    license: Optional[str] = None
    compatibility: Optional[str] = None
    allowed_tools: Optional[list[str]] = None
    metadata_dict: dict[str, Any] | None = None


@dataclass
class SkillActivation:
    """Skill activation result (Tier 2: instructions)."""

    name: str
    body: str  # Full SKILL.md instructions
    resources: dict[str, Path]  # Tier 3 resources


class SkillPlugin(ABC):
    """Abstract base for skill implementations.

    All frameworks must implement these three tiers of skill access:
    - Tier 1 (Metadata): Fast discovery, loaded at startup
    - Tier 2 (Instructions): Full SKILL.md, loaded on activation
    - Tier 3 (Resources): Scripts/refs, loaded on demand

    Session scratch directory
    -------------------------
    Scripts that maintain state between calls (e.g. ``concord.py init`` then
    ``concord.py step``) need a directory that persists across multiple
    ``run_script()`` invocations within the same agent session.

    The scratch directory is always ``<root>/tmp/skills-scratch/`` where
    ``<root>`` is:

    * ``run_dir`` if provided — typically the run output directory
      (``output_dir/`` in the benchmark runner, which already contains
      ``traces/`` and ``checkpoints/``).  This keeps all temporary files
      visible and co-located with the run's other artifacts.
    * A ``tempfile.mkdtemp(prefix="mas-run-")`` directory otherwise — unique
      per plugin instance (= per agent session), cleaned up on ``close()``.

    The two-level layout is intentional: ``tmp/`` is the shared scratch root
    for the whole run; every plugin that needs scratch space uses its own
    named sub-directory inside it (``skills-scratch``, ``memory-scratch``,
    …). This makes all temporary content visible in one place and easy to
    clean up together.
    """

    def __init__(
        self,
        working_dir: Path | None = None,  # explicit override (legacy)
        run_dir: Path | None = None,  # run output dir — preferred
    ) -> None:
        # Explicit override wins (backwards compat); run_dir is the new path.
        self._explicit_working_dir: Path | None = Path(working_dir).resolve() if working_dir else None
        # Do not resolve run_dir — let the OS handle symlinks at mkdir time
        # so paths remain consistent with callers (e.g. /var vs /private/var on macOS).
        self._run_dir: Path | None = Path(run_dir) if run_dir else None
        self._session_root: Path | None = None

    def get_working_dir(self) -> Path:
        """Return ``<scratch-root>/skills-scratch/``, creating it if needed.

        The same path is returned on every call within this plugin instance,
        so scripts that write state files between calls (e.g.
        ``concord-state.json``) can always find them.
        """
        # 1. Explicit override (working_dir= constructor arg, legacy compat).
        if self._explicit_working_dir is not None:
            self._explicit_working_dir.mkdir(parents=True, exist_ok=True)
            return self._explicit_working_dir

        # 2. run_dir provided → output_dir/tmp/skills-scratch/
        if self._run_dir is not None:
            d = self._run_dir / "tmp" / "skills-scratch"
            d.mkdir(parents=True, exist_ok=True)
            return d

        # 3. Auto temp: create a session-level root once, put skills-scratch inside.
        if self._session_root is None:
            self._session_root = Path(tempfile.mkdtemp(prefix="mas-run-"))
            # Nothing else currently calls close() for plugin instances built
            # by the declarative plugin loader (no run_dir/working_dir is
            # threaded through from bootstrap today), so this would otherwise
            # leak a temp directory per session. atexit is a best-effort
            # safety net (skipped on SIGKILL/crash, same as any atexit cleanup).
            atexit.register(self.close)
        d = self._session_root / "tmp" / "skills-scratch"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def session_scratch_root(self) -> Path | None:
        """The ``tmp/`` directory shared by all plugins in this session.

        Returns ``run_dir/tmp/`` when ``run_dir`` was provided, the
        auto-created ``mas-run-XXX/tmp/`` temp root otherwise, or ``None``
        if neither has been initialised yet.
        """
        if self._run_dir is not None:
            return self._run_dir / "tmp"
        if self._session_root is not None:
            return self._session_root / "tmp"
        return None

    def close(self) -> None:
        """Remove the auto-created session root if present.

        When ``run_dir`` was provided the directory is part of the run output
        and is left untouched (the runner owns its cleanup).  Only the
        auto-created temp root is removed.
        """
        if self._session_root is not None and self._session_root.exists():
            shutil.rmtree(self._session_root, ignore_errors=True)
            self._session_root = None

    @abstractmethod
    def discover(self, base_dir: Path) -> dict[str, SkillMetadata]:
        """Tier 1: Discover all skills, return metadata.

        Args:
            base_dir: Root directory to search.

        Returns:
            Dict mapping skill name -> SkillMetadata.
        """

    @abstractmethod
    def activate(self, skill_name: str) -> SkillActivation:
        """Tier 2: Load skill instructions.

        Args:
            skill_name: Name of skill to activate.

        Returns:
            SkillActivation with body + resource paths.
        """

    @abstractmethod
    def read_resource(self, skill_name: str, resource_path: str) -> str:
        """Tier 3: Read a skill resource (script, ref, asset).

        Args:
            skill_name: Name of skill.
            resource_path: Relative path within skill dir (e.g. 'scripts/foo.py').

        Returns:
            File contents as string.
        """

    @abstractmethod
    def run_script(
        self,
        skill_name: str,
        script_name: str,
        args: list[str] | None = None,
        timeout: int = 30,
        env_extra: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Execute a skill script in isolated environment.

        Args:
            skill_name: Name of skill.
            script_name: Script filename (plain name, no path separators).
            args: Command-line arguments.
            timeout: Max execution time in seconds.
            env_extra: Additional environment variables.

        Returns:
            Dict with exit_code, stdout, stderr, ok fields.
        """

    @abstractmethod
    def allowed_tools(self, skill_name: str) -> list[str]:
        """Get list of allowed tools for skill (governance).

        Args:
            skill_name: Name of skill.

        Returns:
            List of tool names (empty = no restrictions).
        """


def run_script_from_source(
    source: str,
    script_name: str,
    args: list[str] | None = None,
    timeout: int = 30,
    env_extra: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Execute a script given its source code.

    Shared helper used by wrapper plugins (LangChain, ADK) that obtain script
    content in-memory (via backend or in-memory resources) and need to run it.

    Writes the source to a secure temp file, executes it, then cleans up.
    ``env_extra`` is **merged** into the current environment (not replacing it).

    Args:
        source: Script source code as a string.
        script_name: Original filename — used only to derive the file suffix
                     (e.g. ``"main.py"`` → suffix ``".py"``).
        args: Command-line arguments to pass.
        timeout: Maximum execution time in seconds.
        env_extra: Additional environment variables to merge in.
        cwd: Working directory for the subprocess.  Pass the plugin's
             ``get_working_dir()`` so scripts that write state files between
             calls (e.g. ``concord-state.json``) find them on the next call.
             Defaults to ``None`` (inherits the current process directory).

    Returns:
        Dict with ``exit_code``, ``stdout``, ``stderr``, ``ok`` fields.
    """
    if args:
        for item in args:
            if not isinstance(item, str):
                raise TypeError(f"'args' items must all be strings, got {type(item).__name__}")

    suffix = Path(script_name).suffix or ".py"

    with tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False, encoding="utf-8") as tmp:
        tmp.write(source)
        tmp_path = Path(tmp.name)

    try:
        if suffix == ".py":
            cmd = [sys.executable, str(tmp_path)]
        elif suffix in [".sh", ".bash"]:
            cmd = ["/bin/bash", str(tmp_path)]
        elif suffix in [".js", ".ts"]:
            cmd = ["node", str(tmp_path)]
        else:
            cmd = [str(tmp_path)]

        cmd.extend(args or [])

        # Always merge env_extra into the current environment.
        # Replacing the full environment (env=env_extra) would strip PATH,
        # HOME, PYTHONPATH, etc. and break most scripts.
        # Sanitized here (not just by callers) so this is safe even when
        # invoked directly, outside the RunSkillScriptPlugin tool-call path.
        from mas.runtime.secrets import child_env

        safe_extra = sanitize_extra_env(env_extra)
        env = child_env(safe_extra) if safe_extra else child_env()

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                cwd=str(cwd) if cwd is not None else None,
                check=False,
            )
            return {
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "ok": result.returncode == 0,
            }
        except subprocess.TimeoutExpired:
            return {
                "exit_code": 124,
                "stdout": "",
                "stderr": f"Script execution timed out after {timeout}s",
                "ok": False,
            }
    finally:
        tmp_path.unlink(missing_ok=True)
