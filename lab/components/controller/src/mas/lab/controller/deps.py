#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared dependencies and helpers for the FastAPI controller API."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from fastapi import HTTPException
from starlette.requests import Request

from mas.lab.controller.constants import HIDDEN_FILES, LIBRARIES_DIR

logger = logging.getLogger(__name__)

_manifest_store = None
_manifest_store_last_refresh = 0.0


def _refresh_interval_seconds() -> float:
    """Return manifest refresh throttle interval.

    Set MAS_CONTROLLER_REFRESH_INTERVAL_S=0 to keep legacy refresh-on-every-call behavior.
    """
    raw = os.environ.get("MAS_CONTROLLER_REFRESH_INTERVAL_S", "1.0").strip()
    try:
        interval = float(raw)
    except ValueError:
        return 1.0
    return max(0.0, interval)


def store_from_request(request: Request):
    """Prefer the app-injected store (tests); else the process singleton."""
    return getattr(request.app.state, "manifest_store", None) or get_manifest_store()


def get_manifest_store():
    """Lazy ManifestStore — discovers *.lab dirs and manifest_libraries."""
    global _manifest_store, _manifest_store_last_refresh
    if _manifest_store is None:
        from mas.lab.controller.manifest_store import ManifestStore

        try:
            from mas.lab.workspace import WorkspaceConfig

            ws = WorkspaceConfig.load()
        except Exception:
            ws = None
        _manifest_store = ManifestStore(ws)
        _manifest_store_last_refresh = time.monotonic()
    else:
        now = time.monotonic()
        interval = _refresh_interval_seconds()
        if interval <= 0.0 or (now - _manifest_store_last_refresh) >= interval:
            _manifest_store.refresh()
            _manifest_store_last_refresh = now
    return _manifest_store


def get_library_path(library_name: str) -> Path:
    """Resolve a library name to its directory path."""
    store = get_manifest_store()
    try:
        return store.library_root(library_name)
    except KeyError:
        pass
    lib_dir = LIBRARIES_DIR / library_name
    if not lib_dir.exists() or not lib_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Library '{library_name}' not found")
    return lib_dir


def discover_tools(base_dir: Path, namespaces: list[str] | None = None) -> list[dict]:
    from mas.lab.controller.artifact_discovery import discover_tools

    return discover_tools(base_dir, namespaces)


def discover_skills(base_dir: Path, namespaces: list[str] | None = None) -> list[dict]:
    from mas.lab.controller.artifact_discovery import discover_skills

    return discover_skills(base_dir, namespaces)


async def run_cli(
    cmd: list[str],
    cwd: Path,
    timeout: int = 60,
    env_override: dict[str, str] | None = None,
) -> dict:
    """Execute a CLI command synchronously (for quick operations like validate)."""
    env = os.environ.copy()
    if env_override:
        env.update(env_override)

    logger.info("Running: %s (cwd=%s)", " ".join(cmd), cwd)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise HTTPException(
            status_code=504,
            detail=f"Command timed out after {timeout}s: {' '.join(cmd)}",
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail=f"Command not found: {cmd[0]}. Ensure mas-runtime is on PATH.",
        )

    result = {
        "exit_code": proc.returncode,
        "stdout": stdout.decode(errors="replace"),
        "stderr": stderr.decode(errors="replace"),
        "command": " ".join(cmd),
    }

    if proc.returncode != 0:
        result["error"] = f"Command failed with exit code {proc.returncode}"

    return result


def build_tree(root: Path, base: Path) -> list[dict]:
    """Recursively build a file/folder tree structure."""
    entries = []
    try:
        children_iter = sorted(root.iterdir())
    except (PermissionError, OSError):
        return entries
    for child in children_iter:
        if child.name in HIDDEN_FILES:
            continue
        rel = str(child.relative_to(base))
        try:
            if child.is_dir():
                children = build_tree(child, base)
                entries.append({"name": child.name, "path": rel, "type": "directory", "children": children})
            else:
                entries.append({"name": child.name, "path": rel, "type": "file", "size": child.stat().st_size})
        except (FileNotFoundError, PermissionError, OSError):
            entries.append({"name": child.name, "path": rel, "type": "file", "size": 0})
    return entries


def ensure_yaml_ext(name: str) -> str:
    """Append .yaml extension when none is present."""
    lower = name.lower()
    if lower.endswith(".yaml") or lower.endswith(".yml"):
        return name
    return f"{name}.yaml"


async def validate_overlay_content(content: str, lib_dir: Path) -> list[str] | None:
    """Validate overlay YAML content. Returns error list or None if valid."""
    from mas.lab.controller.manifest_validation import validate_overlay_yaml_content

    return validate_overlay_yaml_content(content, base_dir=lib_dir)
