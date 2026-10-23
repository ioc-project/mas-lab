#!/usr/bin/env python3
#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Run Ruff on Python files changed against a git base (PR tip vs main)."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def default_base() -> str:
    env_base = os.environ.get("GITHUB_BASE_SHA") or os.environ.get("DIFF_BASE")
    if env_base and not set(env_base) <= {"0"}:
        return env_base
    for ref in ("origin/main", "main"):
        result = subprocess.run(
            ["git", "rev-parse", "--verify", ref],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return ref
    return "HEAD~1"


def _git_name_only(args: list[str]) -> list[str]:
    result = subprocess.run(
        args,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)
    files: list[str] = []
    for raw in result.stdout.splitlines():
        path = raw.strip()
        if path and (REPO_ROOT / path).is_file():
            files.append(path)
    return files


def changed_python_files(base: str, head: str) -> list[str]:
    files = set(
        _git_name_only(
            [
                "git",
                "diff",
                "--name-only",
                "--diff-filter=ACMRT",
                f"{base}...{head}",
                "--",
                "*.py",
            ]
        )
    )
    if head == "HEAD":
        files.update(_git_name_only(["git", "diff", "--name-only", "--diff-filter=ACMRT", "--", "*.py"]))
        files.update(_git_name_only(["git", "diff", "--name-only", "--diff-filter=ACMRT", "--cached", "--", "*.py"]))
        files.update(_git_name_only(["git", "ls-files", "--others", "--exclude-standard", "--", "*.py"]))
    return sorted(files)


def ruff_executable() -> list[str]:
    venv_ruff = Path(sys.executable).parent / "ruff"
    if os.access(venv_ruff, os.X_OK):
        return [str(venv_ruff)]
    found = shutil.which("ruff")
    if found:
        return [found]
    return ["uvx", "ruff"]


def ruff_command(mode: str, files: list[str]) -> list[str]:
    exe = ruff_executable()
    if mode == "check":
        return [*exe, "check", *files]
    if mode == "format-check":
        return [*exe, "format", "--check", *files]
    raise ValueError(f"Unsupported mode: {mode}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["check", "format-check"])
    parser.add_argument("--base", default=None, help="Base git ref or SHA")
    parser.add_argument("--head", default="HEAD", help="Head git ref or SHA")
    parser.add_argument("--root", default="", help="Only files under this repo-relative prefix")
    args = parser.parse_args(argv)

    base = args.base or default_base()
    files = changed_python_files(base=base, head=args.head)
    if args.root:
        prefix = args.root.rstrip("/") + "/"
        files = [path for path in files if path.startswith(prefix) or path == args.root.rstrip("/")]
    if not files:
        print(f"No changed Python files between {base} and {args.head}; skipping Ruff.")
        return 0

    cmd = ruff_command(args.mode, files)
    print("Running:", " ".join(cmd))
    return subprocess.run(cmd, cwd=REPO_ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
