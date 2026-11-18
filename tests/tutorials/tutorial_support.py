#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Paths and helpers shared by the tutorial integration tests.

A module of its own, not ``conftest``: a test that did ``from conftest import ...``
picked up whichever ``conftest`` module another test directory loaded first, so
``pytest runtime/tests tests/tutorials`` failed to collect.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
TUTORIALS = REPO_ROOT / "docs" / "tutorials"
T01 = TUTORIALS / "01-building-an-agent"
T02 = TUTORIALS / "02-creating-a-mas"
T03 = TUTORIALS / "03-experiments-and-analysis"
T06 = TUTORIALS / "06-agent-skills"
T07 = TUTORIALS / "07-slim-link"
T08 = TUTORIALS / "08-telemetry"
T09 = TUTORIALS / "09-kg-scope"
T12 = TUTORIALS / "12-subagents"
T13 = TUTORIALS / "13-control-and-debug"
T14 = TUTORIALS / "14-governance-hitl"


# ---------------------------------------------------------------------------
# Networking helpers
# ---------------------------------------------------------------------------


def free_port() -> int:
    """An OS-assigned free TCP port on localhost, for in-process server tests."""
    import socket

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


# ---------------------------------------------------------------------------
# YAML loading helpers
# ---------------------------------------------------------------------------


def load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# CLI runner helper
# ---------------------------------------------------------------------------


def run_cli(
    args: list[str],
    cwd: Path | None = None,
    timeout: int = 30,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run a CLI command and return the result.

    Resolves the command from the current Python environment's Scripts/bin
    directory so tests work inside a venv without requiring PATH changes.
    """
    # Resolve the executable from the same prefix as the running interpreter
    venv_bin = Path(sys.executable).parent
    exe = venv_bin / args[0]
    if exe.exists():
        resolved_args = [str(exe)] + args[1:]
    else:
        resolved_args = args

    env = {**os.environ, "MAS_MANIFEST_VALIDATE": "1"}
    if extra_env:
        env.update(extra_env)

    return subprocess.run(
        resolved_args,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        timeout=timeout,
        env=env,
    )


def tutorial_copy(src: Path, tmp_path: Path) -> Path:
    """A throwaway copy of a tutorial directory, to run ``mas-ctl chat`` in.

    A chat run writes traces beside its manifest; running on a copy keeps them out of the repo.
    """
    dest = tmp_path / src.name
    shutil.copytree(src, dest)
    return dest


def tutorial_file(root: Path, name: str) -> Path:
    """A tutorial's overlay or Control: governance files are Controls in ``controls/``, patches are in ``overlays/``."""
    return root / ("controls" if name.startswith("governance-") else "overlays") / name
