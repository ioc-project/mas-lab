#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Scripts with PEP 723 inline metadata run through ``uv run --script``."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from sandbox import run_command
from sandbox.runner import _build_command, validate_one_off_command

_INLINE = '# /// script\n# dependencies = ["tomli-w"]\n# ///\nprint(1)\n'


def test_inline_metadata_uses_uv(tmp_path: Path) -> None:
    script = tmp_path / "s.py"
    script.write_text(_INLINE)
    with patch("sandbox.runner.shutil.which", return_value="/bin/uv"):
        assert _build_command(script, ["a"]) == ["/bin/uv", "run", "--script", str(script), "a"]


def test_plain_script_uses_current_interpreter(tmp_path: Path) -> None:
    script = tmp_path / "s.py"
    script.write_text("print(1)\n")
    with patch("sandbox.runner.shutil.which", return_value="/bin/uv"):
        assert _build_command(script, []) == [sys.executable, str(script)]


def test_inline_metadata_without_uv_falls_back(tmp_path: Path) -> None:
    script = tmp_path / "s.py"
    script.write_text(_INLINE)
    with patch("sandbox.runner.shutil.which", return_value=None):
        assert _build_command(script, []) == [sys.executable, str(script)]


# --- one-off package-launcher commands ------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        ["uvx", "ruff@0.8.0", "check", "."],
        ["npx", "eslint@9", "--fix", "."],
        ["bunx", "eslint@9"],
        ["pipx", "run", "black==24.10.0", "."],
        ["deno", "run", "npm:create-vite@6", "app"],
        ["go", "run", "golang.org/x/tools/cmd/goimports@v0.28.0", "."],
    ],
)
def test_one_off_command_accepted(command: list[str]) -> None:
    assert validate_one_off_command(command) is None


@pytest.mark.parametrize(
    "command",
    [
        [],
        ["uvx"],
        ["rm", "-rf", "/"],
        ["bash", "-c", "id"],
        ["pipx", "install", "x"],
        ["go", "build", "."],
        ["deno", "eval", "1"],
    ],
)
def test_one_off_command_rejected(command: list[str]) -> None:
    assert validate_one_off_command(command) is not None


def test_run_command_executes_launcher(tmp_path: Path) -> None:
    fake = tmp_path / "bin"
    fake.mkdir()
    uvx = fake / "uvx"
    uvx.write_text('#!/bin/sh\necho "uvx:$*"\n')
    uvx.chmod(0o755)
    with patch.dict("os.environ", {"PATH": f"{fake}:/usr/bin:/bin"}):
        result = run_command(["uvx", "concord@1.0", "--help"], cwd=tmp_path)
    assert result.ok
    assert result.stdout.strip() == "uvx:concord@1.0 --help"


def test_run_command_reports_missing_launcher(tmp_path: Path) -> None:
    with patch.dict("os.environ", {"PATH": str(tmp_path)}):
        result = run_command(["uvx", "concord"], cwd=tmp_path)
    assert result.exit_code == 127
    assert "not installed" in result.stderr
