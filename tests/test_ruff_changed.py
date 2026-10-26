#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/ruff_changed.py"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "ruff_changed.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("ruff_changed", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def lint():
    return _load_module()


def test_ruff_command_check_includes_files(lint):
    cmd = lint.ruff_command("check", ["scripts/ruff_changed.py"])
    assert cmd[-2:] == ["check", "scripts/ruff_changed.py"]


def test_main_skips_when_no_python_files(lint, monkeypatch, capsys):
    monkeypatch.setattr(lint, "changed_python_files", lambda **_kwargs: [])
    assert lint.main(["check", "--base", "origin/main"]) == 0
    assert "skipping Ruff" in capsys.readouterr().out


def test_default_base_prefers_github_sha(lint, monkeypatch):
    monkeypatch.setenv("GITHUB_BASE_SHA", "abc123")
    assert lint.default_base() == "abc123"


def test_root_filters_changed_files(lint, monkeypatch):
    monkeypatch.setenv("GITHUB_BASE_SHA", "abc123")
    monkeypatch.setattr(
        lint,
        "changed_python_files",
        lambda **_kwargs: ["library-standard/src/a.py", "runtime/src/b.py"],
    )
    seen: list[list[str]] = []

    def fake_ruff(mode: str, files: list[str]) -> list[str]:
        seen.append(files)
        return ["true"]

    monkeypatch.setattr(lint, "ruff_command", fake_ruff)
    monkeypatch.setattr(lint.subprocess, "run", lambda *args, **kwargs: type("R", (), {"returncode": 0})())
    assert lint.main(["check", "--root", "library-standard"]) == 0
    assert seen == [["library-standard/src/a.py"]]


def test_changed_python_files_includes_untracked_when_head_is_head(lint, tmp_path, monkeypatch):
    calls: list[list[str]] = []

    def fake_git(args: list[str]) -> list[str]:
        calls.append(args)
        if "ls-files" in args:
            return ["scripts/ruff_changed.py"]
        return []

    monkeypatch.setattr(lint, "_git_name_only", fake_git)
    files = lint.changed_python_files(base="origin/main", head="HEAD")
    assert "scripts/ruff_changed.py" in files
    assert any("ls-files" in args for args in calls)
