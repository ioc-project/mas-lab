#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for skill_plugin_base.py shared helpers -- run_script_from_source()
and sanitize_extra_env(), used directly by the ADK/LangChain backend plugins
(NativeSkillPlugin uses the external `sandbox` package instead)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from mas.library.skills.plugins.plugin_skills_native import NativeSkillPlugin
from mas.library.skills.plugins.skill_plugin_base import (
    ENV_DENYLIST,
    guard_relative_name,
    guard_resource_realpath,
    run_script_from_source,
    sanitize_extra_env,
    split_resource_category,
)


def test_sanitize_extra_env_drops_denylisted_keys():
    raw = {"PATH": "/tmp/evil", "LD_PRELOAD": "/tmp/mal.so", "MY_CONFIG": "ok"}
    sanitized = sanitize_extra_env(raw)
    assert sanitized == {"MY_CONFIG": "ok"}


def test_sanitize_extra_env_drops_denylisted_keys_case_insensitively():
    raw = {"path": "/tmp/evil", "Ld_Preload": "/tmp/mal.so", "MY_CONFIG": "ok"}
    sanitized = sanitize_extra_env(raw)
    assert sanitized == {"MY_CONFIG": "ok"}


def test_sanitize_extra_env_drops_non_string_values():
    sanitized = sanitize_extra_env({"OK": "value", "BAD": 123, "ALSO_BAD": ["x"]})
    assert sanitized == {"OK": "value"}


def test_sanitize_extra_env_handles_none():
    assert sanitize_extra_env(None) == {}


def test_env_denylist_covers_interpreter_and_loader_vars():
    assert {"PATH", "PYTHONPATH", "LD_PRELOAD", "LD_LIBRARY_PATH"} <= ENV_DENYLIST


def test_run_script_from_source_executes_and_merges_safe_env():
    result = run_script_from_source(
        source="import os; print(os.environ.get('MY_CONFIG', 'MISSING'))\n",
        script_name="show_env.py",
        env_extra={"MY_CONFIG": "hello"},
    )
    assert result["ok"] is True
    assert "hello" in result["stdout"]


def test_run_script_from_source_rejects_denylisted_env_overrides():
    """Defense in depth: run_script_from_source() (used directly by ADK/
    LangChain backend plugins) must sanitize env_extra itself, not rely on
    callers (e.g. sk_shell.py) to have already sanitized it."""
    result = run_script_from_source(
        source=(
            "import os, json; print(json.dumps({"
            "'PATH': os.environ.get('PATH', ''), "
            "'LD_PRELOAD': os.environ.get('LD_PRELOAD', 'MISSING'), "
            "}))\n"
        ),
        script_name="show_env.py",
        env_extra={"PATH": "/tmp/evil", "LD_PRELOAD": "/tmp/mal.so"},
    )
    assert result["ok"] is True
    env_in_script = json.loads(result["stdout"])
    assert env_in_script["PATH"] != "/tmp/evil"
    assert env_in_script["LD_PRELOAD"] == "MISSING"


def test_run_script_from_source_rejects_non_string_args():
    """Defense in depth: mirrors require_str_list_arg's check in sk_shell.py
    -- run_script_from_source() must itself reject non-string args elements,
    since it's a shared helper reachable outside the RunSkillScriptPlugin
    tool-call path (ADK/LangChain backend plugins call it directly)."""
    with pytest.raises(TypeError, match="must all be strings"):
        run_script_from_source(
            source="print('hi')\n",
            script_name="show.py",
            args=[1, 2],
        )


def test_get_working_dir_without_run_dir_registers_atexit_cleanup(tmp_path: Path):
    """No caller currently threads run_dir/working_dir through to plugins
    built by the declarative plugin loader (library.yaml), so every real
    session falls into the auto-temp-dir branch. Without an atexit hook this
    orphans a `mas-run-*` temp directory per session — verify the hook is
    registered exactly when the auto-temp path is taken."""
    plugin = NativeSkillPlugin(base_dir=tmp_path)

    with patch("mas.library.skills.plugins.skill_plugin_base.atexit.register") as mock_register:
        d = plugin.get_working_dir()

    assert d.exists()
    assert d.parent.parent.name.startswith("mas-run-")
    mock_register.assert_called_once_with(plugin.close)


def test_get_working_dir_called_twice_only_registers_atexit_once(tmp_path: Path):
    plugin = NativeSkillPlugin(base_dir=tmp_path)

    with patch("mas.library.skills.plugins.skill_plugin_base.atexit.register") as mock_register:
        plugin.get_working_dir()
        plugin.get_working_dir()

    mock_register.assert_called_once()


def test_close_removes_auto_created_session_root(tmp_path: Path):
    plugin = NativeSkillPlugin(base_dir=tmp_path)
    with patch("mas.library.skills.plugins.skill_plugin_base.atexit.register"):
        plugin.get_working_dir()
    session_root = plugin.session_scratch_root.parent
    assert session_root.exists()

    plugin.close()

    assert not session_root.exists()


def test_get_working_dir_with_explicit_run_dir_does_not_register_atexit(tmp_path: Path):
    """When run_dir IS provided (e.g. a future bootstrap wiring), the scratch
    dir lives under the run's own output directory and is not orphaned --
    no atexit cleanup is needed or registered."""
    plugin = NativeSkillPlugin(base_dir=tmp_path, run_dir=tmp_path / "run-output")

    with patch("mas.library.skills.plugins.skill_plugin_base.atexit.register") as mock_register:
        d = plugin.get_working_dir()

    assert d == tmp_path / "run-output" / "tmp" / "skills-scratch"
    mock_register.assert_not_called()


# ---------------------------------------------------------------------------
# Resource-path traversal guards -- shared by all 3 backends' read_resource()
# ---------------------------------------------------------------------------


def test_split_resource_category_accepts_known_categories():
    assert split_resource_category("references/doc.md") == ("references", "doc.md")
    assert split_resource_category("assets/logo.png") == ("assets", "logo.png")
    assert split_resource_category("scripts/main.py") == ("scripts", "main.py")


def test_split_resource_category_rejects_unknown_category():
    with pytest.raises(ValueError, match="category/name"):
        split_resource_category("../../etc/passwd")


def test_split_resource_category_rejects_missing_slash():
    with pytest.raises(ValueError, match="category/name"):
        split_resource_category("references")


def test_split_resource_category_rejects_traversal_in_name():
    """The category prefix alone isn't enough -- a `..` in the name part
    must also be rejected (this is what protects LangChain's FilesystemBackend,
    which is rooted at base_dir shared across all skills, not just the
    current one)."""
    with pytest.raises(ValueError, match="Path traversal"):
        split_resource_category("references/../../etc/passwd")


def test_guard_relative_name_accepts_nested_subdir():
    assert guard_relative_name("sub/doc.md") == "sub/doc.md"


def test_guard_relative_name_rejects_dotdot():
    with pytest.raises(ValueError, match="Path traversal"):
        guard_relative_name("../secret.txt")


def test_guard_relative_name_rejects_absolute_path():
    with pytest.raises(ValueError, match="Path traversal"):
        guard_relative_name("/etc/passwd")


def test_guard_relative_name_rejects_empty():
    with pytest.raises(ValueError, match="Path traversal"):
        guard_relative_name("")


def test_guard_resource_realpath_accepts_nested_file(tmp_path: Path):
    skill_dir = tmp_path / "my-skill"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "references" / "doc.md").write_text("hi", encoding="utf-8")

    resolved = guard_resource_realpath(skill_dir, "references/doc.md")

    assert resolved == (skill_dir / "references" / "doc.md").resolve()


def test_guard_resource_realpath_rejects_traversal(tmp_path: Path):
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()

    with pytest.raises(ValueError, match="Path traversal"):
        guard_resource_realpath(skill_dir, "../../etc/passwd")


def test_guard_resource_realpath_rejects_symlink_escape(tmp_path: Path):
    """A symlink inside skill_dir pointing outside it must be rejected --
    .resolve() follows symlinks, so relative_to() catches this the same way
    it catches ../ traversal."""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("outside", encoding="utf-8")
    (skill_dir / "link").symlink_to(secret)

    with pytest.raises(ValueError, match="Path traversal"):
        guard_resource_realpath(skill_dir, "link")
