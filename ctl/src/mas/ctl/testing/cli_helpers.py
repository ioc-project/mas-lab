#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""CLI subprocess helpers for integration tests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

_V2_ROOT = Path(__file__).resolve().parents[5]


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def mas_lab_root() -> Path:
    env = os.environ.get("MAS_LAB_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    # Monorepo / Actions checkout (ctl package lives under repo root).
    if (_V2_ROOT / "lab" / "pyproject.toml").is_file():
        return _V2_ROOT
    candidates = (
        _V2_ROOT.parent / "ioc-project" / "mas-lab",
        Path.home() / "repos" / "ioc-project" / "mas-lab",
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[1]


def _v2_env(base: dict[str, str]) -> dict[str, str]:
    paths = [
        _V2_ROOT / "ctl" / "src",
        _V2_ROOT / "runtime" / "src",
        _V2_ROOT / "library-standard" / "src",
    ]
    existing = base.get("PYTHONPATH", "")
    merged = os.pathsep.join(str(p) for p in paths if p.is_dir())
    if existing:
        merged = f"{merged}{os.pathsep}{existing}" if merged else existing
    return {**base, "PYTHONPATH": merged}


def run_cli(args: list[str], cwd: Path | None = None, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run v2 CLIs via PYTHONPATH; mas-lab via ``uv run`` in mas-lab checkout."""
    if not args:
        raise ValueError("empty command")

    env = _v2_env({**os.environ, "MAS_MANIFEST_VALIDATE": "1"})

    if args[0] == "mas-lab":
        lab = mas_lab_root()
        if lab.is_dir() and (lab / "pyproject.toml").is_file():
            which = subprocess.run(
                ["uv", "run", "--directory", str(lab), "which", "mas-lab"],
                capture_output=True,
                text=True,
                env={**os.environ},
            )
            mas_lab_bin = which.stdout.strip()
            if mas_lab_bin and Path(mas_lab_bin).is_file():
                return subprocess.run(
                    [mas_lab_bin, *args[1:]],
                    capture_output=True,
                    text=True,
                    cwd=str(cwd) if cwd else str(lab),
                    timeout=timeout,
                    env={**os.environ, "MAS_MANIFEST_VALIDATE": "1"},
                )
            cmd = ["uv", "run", "--directory", str(lab), "mas-lab", *args[1:]]
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(cwd) if cwd else str(lab),
                timeout=timeout,
                env={**os.environ, "MAS_MANIFEST_VALIDATE": "1"},
            )

    if args[0] == "mas-runtime":
        rewritten = ["mas-ctl", *args[1:]]
        if len(rewritten) >= 2 and rewritten[1] == "run-agent":
            rewritten[1] = "chat"
        cmd = [sys.executable, "-m", "mas.ctl.cli.main", *rewritten[1:]]
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(cwd) if cwd else None,
            timeout=timeout,
            env=env,
        )

    if args[0] == "mas-ctl":
        cmd = [sys.executable, "-m", "mas.ctl.cli.main", *args[1:]]
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(cwd) if cwd else None,
            timeout=timeout,
            env=env,
        )

    venv_bin = Path(sys.executable).parent
    exe = venv_bin / args[0]
    resolved = [str(exe)] + args[1:] if exe.exists() else args
    return subprocess.run(
        resolved,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        timeout=timeout,
        env=env,
    )
