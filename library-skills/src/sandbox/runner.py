#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Sandbox utilities — script execution, environment filtering, resource limits."""

from __future__ import annotations

import logging
import os
import re
import shutil
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
        # uv config so PEP 723 scripts can install packages (cache, mirrors, proxies)
        "UV_CACHE_DIR",
        "UV_INDEX_URL",
        "UV_EXTRA_INDEX_URL",
        "UV_DEFAULT_INDEX",
        "UV_PYTHON",
        "UV_OFFLINE",
        "UV_NO_CACHE",
        "UV_NATIVE_TLS",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
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

    return _execute(
        _build_command(script_path, args),
        cwd=cwd,
        timeout_val=timeout_val,
        env_extra=env_extra,
        stdin=stdin,
        label=str(script_path),
    )


# Package launchers a skill may invoke as a one-off command, mapped to the
# subcommand each requires (None = runs the package directly).
# https://agentskills.io/skill-creation/using-scripts#one-off-commands
ONE_OFF_LAUNCHERS: dict[str, str | None] = {
    "uvx": None,
    "npx": None,
    "bunx": None,
    "pipx": "run",
    "deno": "run",
    "go": "run",
}


def validate_one_off_command(command: list[str]) -> str | None:
    """Return an error message if *command* is not an allowed one-off launcher call."""
    if not command:
        return "command must not be empty"
    launcher = command[0]
    if launcher not in ONE_OFF_LAUNCHERS:
        return f"command must start with one of: {sorted(ONE_OFF_LAUNCHERS)} (got {launcher!r})"
    sub = ONE_OFF_LAUNCHERS[launcher]
    if sub is not None and command[1:2] != [sub]:
        return f"{launcher} commands must be '{launcher} {sub} ...'"
    if len(command) < (3 if sub else 2):
        return f"{launcher} needs a package to run"
    return None


def run_command(
    command: list[str],
    cwd: Path,
    timeout: int | None = None,
    env_extra: dict[str, str] | None = None,
    stdin: str = "",
) -> ScriptResult:
    """Run a one-off package-launcher command (uvx, npx, ...) in the sandbox.

    Same environment filtering, resource limits and timeout as run_script().
    The command is an argv list, never a shell string.
    """
    error = validate_one_off_command(command)
    if error:
        return ScriptResult(exit_code=2, stdout="", stderr=error)
    cwd = Path(cwd).resolve()
    safe_env = _build_safe_env(cwd, env_extra or {})
    exe = shutil.which(command[0], path=safe_env.get("PATH"))
    if exe is None:
        return ScriptResult(
            exit_code=127,
            stdout="",
            stderr=f"{command[0]!r} is not installed or not on PATH",
        )
    return _execute(
        [exe, *command[1:]],
        cwd=cwd,
        timeout_val=min(int(timeout or _DEFAULT_TIMEOUT), _MAX_TIMEOUT),
        env_extra=env_extra or {},
        stdin=stdin,
        label=" ".join(command),
    )


def _execute(
    cmd: list[str],
    *,
    cwd: Path,
    timeout_val: int,
    env_extra: dict[str, str],
    stdin: str,
    label: str,
) -> ScriptResult:
    safe_env = _build_safe_env(cwd, env_extra)

    logger.info("Executing: %s (timeout=%ds)", label, timeout_val)

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

# PEP 723 inline metadata opener, e.g. ``# /// script``
_PEP723_RE = re.compile(r"^# /// script\s*$", re.MULTILINE)


def _has_inline_metadata(script_path: Path) -> bool:
    """True if *script_path* declares PEP 723 inline dependencies."""
    try:
        with script_path.open(encoding="utf-8") as fh:
            head = fh.read(4096)
    except (OSError, UnicodeDecodeError):
        return False
    return _PEP723_RE.search(head) is not None


def _build_command(script_path: Path, args: list[str]) -> list[str]:
    """Return the subprocess command list for *script_path*."""
    suffix = script_path.suffix.lower()
    str_args = [str(a) for a in args]
    if suffix == ".py" and (uv := shutil.which("uv")) and _has_inline_metadata(script_path):
        # Dependencies declared inline are installed into a cached, isolated env
        return [uv, "run", "--script", str(script_path)] + str_args
    if suffix == ".py":
        return [sys.executable, str(script_path)] + str_args
    if suffix == ".sh":
        return ["/bin/sh", str(script_path)] + str_args
    # No extension or unknown — assume executable with shebang
    return [str(script_path)] + str_args


def _build_safe_env(cwd: Path, extra_env: dict[str, str]) -> dict[str, str]:
    """Build a sanitized environment for the subprocess."""
    safe = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS}
    safe["PWD"] = str(cwd)
    # Caller-provided overrides — validated to be non-empty strings only
    for k, v in extra_env.items():
        if isinstance(k, str) and k and isinstance(v, str):
            safe[k] = v
    return safe


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
