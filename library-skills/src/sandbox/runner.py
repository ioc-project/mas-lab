#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Sandbox utilities — script execution, environment filtering, resource limits."""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30
_MAX_TIMEOUT = 120

# Environment variables that are safe to pass through to scripts.
# All other env vars (API keys, tokens, credentials, etc.) are stripped.
_SAFE_ENV_KEYS: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "TMPDIR",
        "TMP",
        "TEMP",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LC_MESSAGES",
        "PYTHONPATH",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONUNBUFFERED",
        "VIRTUAL_ENV",
        "VIRTUAL_ENV_PROMPT",
        "LD_LIBRARY_PATH",
        "UV_PROJECT_ENVIRONMENT",
        "PWD",
        "USER",
        "LOGNAME",
    }
)

# POSIX resource limits (best-effort — ignored on platforms without resource module)
_POSIX_CPU_LIMIT_SECS = 60  # hard upper bound on CPU time
_POSIX_MEM_LIMIT_BYTES = 512 * 1024 * 1024  # 512 MiB address space


@dataclass
class ScriptResult:
    """Result of script execution.

    The `ok` field is automatically set based on exit_code during __post_init__.
    """

    exit_code: int
    stdout: str
    stderr: str
    ok: bool = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "ok", self.exit_code == 0)


def run_script(
    script_path: Path,
    args: list[str] | None = None,
    cwd: Path | None = None,
    timeout: int | None = None,
    env_extra: dict[str, str] | None = None,
    stdin: str = "",
) -> ScriptResult:
    """Execute a script in a sandboxed subprocess.

    Args:
        script_path: Path to the script file.
        args: Arguments to pass to the script.
        cwd: Working directory (default: script's parent directory).
        timeout: Execution timeout in seconds (default: 30, max: 120).
        env_extra: Additional environment variables (merged after safe base).
        stdin: Text to feed to the script's stdin. Always passed through as
            subprocess.run's `input=`, even when empty — this closes stdin
            with an immediate EOF rather than leaving it to inherit this
            process's own stdin, which never produces data or EOF here and
            would leave any script that reads stdin blocked until timeout.

    Returns:
        ScriptResult with exit_code, stdout, stderr, ok fields.

    Features:
        - Environment filtering (strips API keys, tokens, credentials)
        - POSIX resource limits (CPU, address space)
        - Timeout enforcement
        - Automatic interpreter selection (.py, .sh, shebang)
    """
    script_path = Path(script_path).resolve()
    cwd = Path(cwd or script_path.parent).resolve()
    args = args or []
    timeout_val = min(int(timeout or _DEFAULT_TIMEOUT), _MAX_TIMEOUT)
    env_extra = env_extra or {}

    if not script_path.is_file():
        return ScriptResult(
            exit_code=127,
            stdout="",
            stderr=f"Script not found: {script_path}",
        )

    cmd = _build_command(script_path, args)
    safe_env = _build_safe_env(cwd, env_extra)

    logger.info(
        "Executing script: %s (args=%s, timeout=%ds)",
        script_path,
        args,
        timeout_val,
    )

    try:
        result = subprocess.run(
            cmd,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout_val,
            cwd=str(cwd),
            env=safe_env,
            check=False,
            preexec_fn=_posix_set_limits if sys.platform != "win32" else None,
        )
    except subprocess.TimeoutExpired:
        return ScriptResult(
            exit_code=124,
            stdout="",
            stderr=f"Script timed out after {timeout_val}s",
        )
    except OSError as exc:
        return ScriptResult(
            exit_code=126,
            stdout="",
            stderr=f"Cannot execute script: {exc}",
        )

    return ScriptResult(
        exit_code=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def guard_path_traversal(script_name: str, base_dir: Path) -> Path | None:
    """Guard against path traversal attacks.

    Accepts plain filename only (no directory separators).
    Returns the resolved path if valid, or None if rejected.
    """
    script_name = script_name.strip()
    if not script_name:
        return None

    script_name_only = Path(script_name).name
    if script_name_only != script_name:
        # Contains directory separators or other path components
        return None

    resolved = (base_dir / script_name_only).resolve()
    try:
        resolved.relative_to(base_dir.resolve())
        return resolved
    except ValueError:
        # Path escapes base_dir
        return None


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _build_command(script_path: Path, args: list[str]) -> list[str]:
    """Return the subprocess command list for *script_path*."""
    suffix = script_path.suffix.lower()
    str_args = [str(a) for a in args]
    if suffix == ".py":
        return [sys.executable, str(script_path)] + str_args
    if suffix == ".sh":
        return ["/bin/sh", str(script_path)] + str_args
    # No extension or unknown — assume executable with shebang
    return [str(script_path)] + str_args


def _build_safe_env(cwd: Path, extra_env: dict[str, str]) -> dict[str, str]:
    """Build a sanitized environment for the subprocess. Host secrets stay out."""
    from mas.runtime import hostenv
    from mas.runtime.secrets import child_env

    snap = hostenv.snapshot()
    declared = {k: snap[k] for k in _SAFE_ENV_KEYS if k in snap}
    for k, v in extra_env.items():
        if isinstance(k, str) and k and isinstance(v, str):
            declared[k] = v
    declared["PWD"] = str(cwd)
    return child_env(declared)


def _posix_set_limits() -> None:
    """Apply POSIX resource limits before exec (called in child process)."""
    try:
        import resource as _resource

        # CPU time — hard cap prevents spin-loops from consuming indefinitely
        try:
            _resource.setrlimit(
                _resource.RLIMIT_CPU,
                (_POSIX_CPU_LIMIT_SECS, _POSIX_CPU_LIMIT_SECS),
            )
        except (OSError, ValueError):
            pass  # already stricter or not supported
        # Address space — prevents memory-bomb scripts
        try:
            _resource.setrlimit(
                _resource.RLIMIT_AS,
                (_POSIX_MEM_LIMIT_BYTES, _POSIX_MEM_LIMIT_BYTES),
            )
        except (OSError, ValueError):
            pass
    except ImportError:
        pass  # Windows or restricted environments
