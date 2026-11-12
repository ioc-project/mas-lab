#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""RunSkillScriptPlugin — ToolContract for executing skill support scripts.

Uses portable sandbox library (skill-sandbox) for environment filtering,
resource limits, and path traversal protection.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agentskills import SkillRegistry
from mas.runtime.contracts.tool_contract import ToolContract
from sandbox import guard_path_traversal, run_script

from .skill_plugin_base import (
    require_dict_arg,
    require_number_arg,
    require_str_arg,
    require_str_list_arg,
    sanitize_extra_env,
)
from .skill_plugin_registry import SkillPluginRegistry, coerce_skill_impl

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30
_MAX_TIMEOUT = 120


class RunSkillScriptPlugin(ToolContract):
    """ToolContract providing run_skill_script — executes bundled skill scripts."""

    kernel_state = True  # reads the session's skill state: needs the live ctx

    def __init__(
        self,
        impl: str = "native",
        base_dir: str | Path | None = None,
    ) -> None:
        super().__init__()
        self._impl = coerce_skill_impl(impl)
        self._base_dir = Path(base_dir).resolve() if base_dir else None
        self._local_backend_plugin: Any | None = None

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "run_skill_script",
                "description": (
                    "Execute a support script from a skill's scripts/ directory. "
                    "Returns stdout, stderr, and exit code. "
                    "Scripts run in a sandboxed environment with resource limits."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill": {
                            "type": "string",
                            "description": "Skill name as listed in the catalog.",
                        },
                        "script": {
                            "type": "string",
                            "description": "Script filename (plain name, no path).",
                        },
                        "args": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Positional arguments.",
                        },
                        "timeout": {
                            "type": "integer",
                            "description": f"Timeout in seconds (default: {_DEFAULT_TIMEOUT}, max: {_MAX_TIMEOUT}).",
                        },
                        "env": {
                            "type": "object",
                            "description": (
                                "Additional environment variables (non-sensitive only). "
                                "Interpreter/loader control variables (PATH, PYTHONPATH, "
                                "LD_PRELOAD, etc.) are rejected."
                            ),
                        },
                        "stdin": {
                            "type": "string",
                            "description": (
                                "Text to feed to the script's stdin (optional). Use this "
                                "for a script documented as reading a reply from stdin — "
                                "passing text via `args` or `env` never reaches it."
                            ),
                        },
                    },
                    "required": ["skill", "script"],
                },
                "semantics": {"concept": "skill", "op": "execute", "subject_arg": "skill"},
            }
        ]

    def on_execute_tool(self, tool_name: str, arguments: dict[str, Any], **kwargs: Any) -> Any:
        if tool_name != "run_skill_script":
            return None
        ctx = kwargs.get("ctx")
        registry = getattr(ctx, "skill_registry", None)
        backend_plugin = _backend_plugin_from_ctx(
            ctx,
            impl=self._impl,
            base_dir=self._base_dir,
            local_cache=self,
        )
        try:
            skill = require_str_arg(arguments, "skill")
            script = require_str_arg(arguments, "script")
            args = require_str_list_arg(arguments, "args")
            env = require_dict_arg(arguments, "env")
            timeout = min(require_number_arg(arguments, "timeout", _DEFAULT_TIMEOUT), _MAX_TIMEOUT)
            stdin = require_str_arg(arguments, "stdin")
        except TypeError as exc:
            return {"error": str(exc)}
        return self._run_script(
            skill=skill,
            script=script,
            args=args,
            timeout=timeout,
            extra_env=sanitize_extra_env(env),
            registry=registry,
            backend_plugin=backend_plugin,
            stdin=stdin,
        )

    def on_collect_tools(self, **_: Any) -> list[dict[str, Any]]:
        return self.list_tools()

    def _run_script(
        self,
        skill: str,
        script: str,
        args: list[str],
        timeout: int,
        extra_env: dict[str, str],
        registry: SkillRegistry | None,
        backend_plugin: Any | None,
        stdin: str = "",
    ) -> dict[str, Any]:
        if not skill:
            return {"error": "skill is required"}
        if not script:
            return {"error": "script is required"}

        record = registry.get(skill) if registry else None
        if record is None:
            available = registry.names() if registry else []
            return {"error": f"Skill {skill!r} not found in registry. Available: {available}"}

        if backend_plugin is not None:
            try:
                try:
                    result = backend_plugin.run_script(
                        skill_name=skill,
                        script_name=script,
                        args=args,
                        timeout=timeout,
                        env_extra=extra_env,
                        stdin=stdin,
                    )
                except TypeError:
                    result = backend_plugin.run_script(
                        skill_name=skill,
                        script_name=script,
                        args=args,
                        timeout=timeout,
                        env_extra=extra_env,
                    )
            except Exception as exc:
                return {"error": f"Cannot run script {script!r} for skill {skill!r}: {exc}"}
            return {
                "skill": skill,
                "script": script,
                "exit_code": result.get("exit_code"),
                "stdout": result.get("stdout", ""),
                "stderr": result.get("stderr", ""),
                "ok": bool(result.get("ok", False)),
            }

        scripts_dir = (record.base_dir / "scripts").resolve()
        if not scripts_dir.is_dir():
            return {"error": f"Skill {skill!r} has no scripts/ directory"}

        # Path traversal guard
        script_path = guard_path_traversal(script, scripts_dir)
        if script_path is None:
            return {"error": f"Invalid script name: {script!r} (must be plain filename, no path separators)"}

        if not script_path.is_file():
            available = sorted(p.name for p in scripts_dir.iterdir() if p.is_file())
            return {
                "error": f"Script {script!r} not found in skill {skill!r}/scripts/",
                "available_scripts": available,
            }

        logger.info(
            "RunSkillScriptPlugin: executing skill=%r script=%r args=%r timeout=%ds",
            skill,
            script,
            args,
            timeout,
        )

        # Use sandbox.run_script(). Defensive try/except: args/env are
        # validated upfront (require_str_list_arg/sanitize_extra_env), but a
        # clean {"error": ...} beats an uncaught exception crashing the tool
        # call if the sandbox package ever raises something unexpected
        # (mirrors the try/except already used for the backend_plugin path
        # above).
        try:
            result = run_script(
                script_path=script_path,
                args=args,
                cwd=record.base_dir,
                timeout=timeout,
                env_extra=extra_env,
                stdin=stdin,
            )
        except Exception as exc:
            return {"error": f"Cannot run script {script!r} for skill {skill!r}: {exc}"}

        logger.debug(
            "RunSkillScriptPlugin: exit_code=%d",
            result.exit_code,
        )

        return {
            "skill": skill,
            "script": script,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "ok": result.ok,
        }


def _backend_plugin_from_ctx(
    ctx: Any,
    *,
    impl: str,
    base_dir: Path | None,
    local_cache: RunSkillScriptPlugin,
) -> Any | None:
    plugin = getattr(ctx, "skill_backend_plugin", None) if ctx is not None else None
    if plugin is not None:
        return plugin
    if impl == "native":
        return None
    if local_cache._local_backend_plugin is None:
        resolved_base = (base_dir or Path.cwd()).resolve()
        plugin = SkillPluginRegistry(impl=impl).get_plugin(base_dir=resolved_base)
        plugin.discover(resolved_base)
        local_cache._local_backend_plugin = plugin
    return local_cache._local_backend_plugin
