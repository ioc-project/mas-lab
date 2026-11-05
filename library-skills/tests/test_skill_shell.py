#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for RunSkillScriptPlugin — ToolContract shell execution of skill scripts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mas.library.skills.lib.registry import SkillRecord, SkillRegistry
from mas.library.skills.plugins.plugin_skills_native import NativeSkillPlugin
from mas.library.skills.plugins.sk_shell import RunSkillScriptPlugin


def _registry_with_scripts(tmp_path: Path, name: str = "my-skill") -> SkillRegistry:
    skill_dir = tmp_path / name
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        f"---\nname: {name}\ndescription: Does something.\n---\n# Body\n",
        encoding="utf-8",
    )
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir()
    # Write a simple Python script
    (scripts_dir / "hello.py").write_text("import sys\nprint('hello', *sys.argv[1:])\n", encoding="utf-8")
    # Write a script that exits non-zero
    (scripts_dir / "fail.py").write_text("import sys; sys.exit(1)\n", encoding="utf-8")

    reg = SkillRegistry()
    reg.register(SkillRecord(name=name, description="Does something.", path=skill_md))
    return reg


class _FakeCtx:
    def __init__(self, registry: SkillRegistry | None = None):
        if registry is not None:
            self.skill_registry = registry


# ---------------------------------------------------------------------------
# run_skill_script — basic execution
# ---------------------------------------------------------------------------


def test_run_python_script_success(tmp_path: Path):
    reg = _registry_with_scripts(tmp_path)
    plugin = RunSkillScriptPlugin()
    result = plugin.on_execute_tool(
        "run_skill_script",
        {"skill": "my-skill", "script": "hello.py"},
        ctx=_FakeCtx(reg),
    )
    assert result["ok"] is True
    assert result["exit_code"] == 0
    assert "hello" in result["stdout"]


def test_run_python_script_with_stdin(tmp_path: Path):
    reg = _registry_with_scripts(tmp_path)
    scripts_dir = tmp_path / "my-skill" / "scripts"
    (scripts_dir / "echo_stdin.py").write_text("import sys\nprint(sys.stdin.read(), end='')\n", encoding="utf-8")
    plugin = RunSkillScriptPlugin()
    result = plugin.on_execute_tool(
        "run_skill_script",
        {"skill": "my-skill", "script": "echo_stdin.py", "stdin": "raw_reject: 15\n"},
        ctx=_FakeCtx(reg),
    )
    assert result["ok"] is True
    assert "raw_reject: 15" in result["stdout"]


def test_run_python_script_with_args(tmp_path: Path):
    reg = _registry_with_scripts(tmp_path)
    plugin = RunSkillScriptPlugin()
    result = plugin.on_execute_tool(
        "run_skill_script",
        {"skill": "my-skill", "script": "hello.py", "args": ["world", "foo"]},
        ctx=_FakeCtx(reg),
    )
    assert "world" in result["stdout"]
    assert "foo" in result["stdout"]


def test_run_script_nonzero_exit(tmp_path: Path):
    reg = _registry_with_scripts(tmp_path)
    plugin = RunSkillScriptPlugin()
    result = plugin.on_execute_tool(
        "run_skill_script",
        {"skill": "my-skill", "script": "fail.py"},
        ctx=_FakeCtx(reg),
    )
    assert result["ok"] is False
    assert result["exit_code"] == 1


# ---------------------------------------------------------------------------
# Security guards
# ---------------------------------------------------------------------------


def test_traversal_via_path_separator_rejected(tmp_path: Path):
    reg = _registry_with_scripts(tmp_path)
    plugin = RunSkillScriptPlugin()
    result = plugin.on_execute_tool(
        "run_skill_script",
        {"skill": "my-skill", "script": "../SKILL.md"},
        ctx=_FakeCtx(reg),
    )
    assert "error" in result
    # Should reject path with directory component
    assert "plain filename" in result["error"] or "escapes" in result["error"]


def test_script_not_found_lists_available(tmp_path: Path):
    reg = _registry_with_scripts(tmp_path)
    plugin = RunSkillScriptPlugin()
    result = plugin.on_execute_tool(
        "run_skill_script",
        {"skill": "my-skill", "script": "nonexistent.py"},
        ctx=_FakeCtx(reg),
    )
    assert "error" in result
    assert "available_scripts" in result
    assert "hello.py" in result["available_scripts"]


def test_skill_without_scripts_dir(tmp_path: Path):
    skill_dir = tmp_path / "no-scripts"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("---\nname: no-scripts\ndescription: x\n---\n", encoding="utf-8")
    reg = SkillRegistry()
    reg.register(SkillRecord(name="no-scripts", description="x", path=skill_md))
    plugin = RunSkillScriptPlugin()
    result = plugin.on_execute_tool(
        "run_skill_script",
        {"skill": "no-scripts", "script": "foo.py"},
        ctx=_FakeCtx(reg),
    )
    assert "error" in result
    assert "no scripts/" in result["error"]


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_missing_skill_arg(tmp_path: Path):
    reg = _registry_with_scripts(tmp_path)
    plugin = RunSkillScriptPlugin()
    result = plugin.on_execute_tool("run_skill_script", {"script": "hello.py"}, ctx=_FakeCtx(reg))
    assert "error" in result


def test_missing_script_arg(tmp_path: Path):
    reg = _registry_with_scripts(tmp_path)
    plugin = RunSkillScriptPlugin()
    result = plugin.on_execute_tool("run_skill_script", {"skill": "my-skill"}, ctx=_FakeCtx(reg))
    assert "error" in result


def test_non_string_args_element_rejected(tmp_path: Path):
    """A malformed call (args bypassing JSON-schema validation, e.g. ints
    instead of strings) must be rejected with a clean error -- not crash
    with an uncaught TypeError deep inside subprocess.run()."""
    reg = _registry_with_scripts(tmp_path)
    plugin = RunSkillScriptPlugin()
    result = plugin.on_execute_tool(
        "run_skill_script",
        {"skill": "my-skill", "script": "hello.py", "args": [1, 2]},
        ctx=_FakeCtx(reg),
    )
    assert "error" in result
    assert "must all be strings" in result["error"]


def test_non_list_args_rejected(tmp_path: Path):
    reg = _registry_with_scripts(tmp_path)
    plugin = RunSkillScriptPlugin()
    result = plugin.on_execute_tool(
        "run_skill_script",
        {"skill": "my-skill", "script": "hello.py", "args": "not-a-list"},
        ctx=_FakeCtx(reg),
    )
    assert "error" in result
    assert "must be a list" in result["error"]


def test_non_dict_env_rejected(tmp_path: Path):
    """dict(env) on a non-dict value (e.g. a string) would otherwise raise
    an uncaught ValueError/TypeError instead of a clean tool error."""
    reg = _registry_with_scripts(tmp_path)
    plugin = RunSkillScriptPlugin()
    result = plugin.on_execute_tool(
        "run_skill_script",
        {"skill": "my-skill", "script": "hello.py", "env": "not-a-dict"},
        ctx=_FakeCtx(reg),
    )
    assert "error" in result
    assert "must be a dict" in result["error"]


def test_non_numeric_timeout_rejected(tmp_path: Path):
    """int(timeout) on a non-numeric value would otherwise raise an
    uncaught ValueError instead of a clean tool error."""
    reg = _registry_with_scripts(tmp_path)
    plugin = RunSkillScriptPlugin()
    result = plugin.on_execute_tool(
        "run_skill_script",
        {"skill": "my-skill", "script": "hello.py", "timeout": "soon"},
        ctx=_FakeCtx(reg),
    )
    assert "error" in result
    assert "must be a number" in result["error"]


def test_bool_timeout_rejected(tmp_path: Path):
    """bool is a subclass of int in Python -- explicitly reject it so
    `timeout: true` doesn't silently become `timeout=1`."""
    reg = _registry_with_scripts(tmp_path)
    plugin = RunSkillScriptPlugin()
    result = plugin.on_execute_tool(
        "run_skill_script",
        {"skill": "my-skill", "script": "hello.py", "timeout": True},
        ctx=_FakeCtx(reg),
    )
    assert "error" in result
    assert "must be a number" in result["error"]


def test_unknown_skill(tmp_path: Path):
    reg = _registry_with_scripts(tmp_path)
    plugin = RunSkillScriptPlugin()
    result = plugin.on_execute_tool(
        "run_skill_script",
        {"skill": "ghost", "script": "hello.py"},
        ctx=_FakeCtx(reg),
    )
    assert "error" in result
    assert "ghost" in result["error"]


def test_no_registry():
    plugin = RunSkillScriptPlugin()
    result = plugin.on_execute_tool(
        "run_skill_script",
        {"skill": "x", "script": "y.py"},
        ctx=_FakeCtx(None),
    )
    assert "error" in result


# ---------------------------------------------------------------------------
# on_collect_tools schema
# ---------------------------------------------------------------------------


def test_list_tools_schema():
    plugin = RunSkillScriptPlugin()
    tools = plugin.list_tools()
    assert len(tools) == 1
    assert tools[0]["name"] == "run_skill_script"
    params = tools[0]["parameters"]
    assert "skill" in params["properties"]
    assert "script" in params["properties"]
    assert "args" in params["properties"]
    assert "timeout" in params["properties"]
    assert "env" in params["properties"]  # extra env support
    assert "stdin" in params["properties"]
    assert set(params["required"]) == {"skill", "script"}
    assert tools[0]["semantics"] == {
        "concept": "skill",
        "op": "execute",
        "subject_arg": "skill",
    }


# ---------------------------------------------------------------------------
# Sandbox — environment sanitization
# ---------------------------------------------------------------------------


def test_sanitized_env_strips_sensitive_vars(tmp_path: Path):
    """Scripts should NOT receive API keys or other sensitive env vars."""
    import os

    # Inject a fake sensitive variable
    os.environ["FAKE_SECRET_TOKEN"] = "my-secret"

    reg = _registry_with_scripts(tmp_path)
    scripts_dir = tmp_path / "my-skill" / "scripts"
    # Script that prints all env vars — we verify the secret isn't there
    (scripts_dir / "dump_env.py").write_text(
        "import os, json; print(json.dumps(dict(os.environ)))\n",
        encoding="utf-8",
    )
    plugin = RunSkillScriptPlugin()
    result = plugin.on_execute_tool(
        "run_skill_script",
        {"skill": "my-skill", "script": "dump_env.py"},
        ctx=_FakeCtx(reg),
    )
    assert result["ok"] is True
    import json

    env_in_script = json.loads(result["stdout"])
    assert "FAKE_SECRET_TOKEN" not in env_in_script, "sensitive env var must not leak into script subprocess"

    del os.environ["FAKE_SECRET_TOKEN"]


def test_extra_env_passed_through(tmp_path: Path):
    """Caller-provided extra_env is available to the script."""
    reg = _registry_with_scripts(tmp_path)
    scripts_dir = tmp_path / "my-skill" / "scripts"
    (scripts_dir / "show_env.py").write_text(
        "import os; print(os.environ.get('MY_CONFIG', 'MISSING'))\n",
        encoding="utf-8",
    )
    plugin = RunSkillScriptPlugin()
    result = plugin.on_execute_tool(
        "run_skill_script",
        {"skill": "my-skill", "script": "show_env.py", "env": {"MY_CONFIG": "hello"}},
        ctx=_FakeCtx(reg),
    )
    assert result["ok"] is True
    assert "hello" in result["stdout"]


def test_extra_env_rejects_denylisted_overrides(tmp_path: Path):
    """Model-supplied env overrides must not touch interpreter/loader control vars.

    PATH, PYTHONPATH, LD_PRELOAD, etc. can subvert the sandbox (e.g. injecting
    a malicious shared library or substituting a trusted binary) — these keys
    must be silently dropped, not merged into the subprocess environment.
    """
    reg = _registry_with_scripts(tmp_path)
    scripts_dir = tmp_path / "my-skill" / "scripts"
    (scripts_dir / "show_env.py").write_text(
        "import os, json; print(json.dumps({"
        "'PATH': os.environ.get('PATH', ''), "
        "'LD_PRELOAD': os.environ.get('LD_PRELOAD', 'MISSING'), "
        "}))\n",
        encoding="utf-8",
    )
    plugin = RunSkillScriptPlugin()
    result = plugin.on_execute_tool(
        "run_skill_script",
        {
            "skill": "my-skill",
            "script": "show_env.py",
            "env": {"PATH": "/tmp/evil", "LD_PRELOAD": "/tmp/mal.so"},
        },
        ctx=_FakeCtx(reg),
    )
    assert result["ok"] is True
    import json

    env_in_script = json.loads(result["stdout"])
    assert env_in_script["PATH"] != "/tmp/evil"
    assert env_in_script["LD_PRELOAD"] == "MISSING"


def test_cwd_is_skill_base_dir(tmp_path: Path):
    """Script's working directory is the skill's base directory."""
    reg = _registry_with_scripts(tmp_path)
    scripts_dir = tmp_path / "my-skill" / "scripts"
    (scripts_dir / "show_cwd.py").write_text("import os; print(os.getcwd())\n", encoding="utf-8")
    plugin = RunSkillScriptPlugin()
    result = plugin.on_execute_tool(
        "run_skill_script",
        {"skill": "my-skill", "script": "show_cwd.py"},
        ctx=_FakeCtx(reg),
    )
    assert result["ok"] is True
    skill_base = str((tmp_path / "my-skill").resolve())
    assert skill_base in result["stdout"].strip()


# ---------------------------------------------------------------------------
# run_skill_script -- routed through an injected backend plugin
# (this is the code path actually used for the ADK/LangChain backends; see
# _backend_plugin_from_ctx() in sk_shell.py -- the native tests above all
# exercise the `backend_plugin is None` branch only)
# ---------------------------------------------------------------------------


def test_run_script_via_injected_backend_plugin_executes_for_real(tmp_path: Path):
    """End-to-end: command execution actually runs when routed through a
    backend plugin (ctx.skill_backend_plugin), not just when native's
    inline sandbox.run_script() call is used directly."""
    reg = _registry_with_scripts(tmp_path)

    # NativeSkillPlugin's own discovery (independent of the SkillRegistry
    # above, which only gates the tool's up-front existence check) looks
    # under <base_dir>/skills/<name>/, not <base_dir>/<name>/ directly.
    backend_skill_dir = tmp_path / "skills" / "my-skill"
    backend_scripts_dir = backend_skill_dir / "scripts"
    backend_scripts_dir.mkdir(parents=True)
    (backend_skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: Does something.\n---\n# Body\n",
        encoding="utf-8",
    )
    (backend_scripts_dir / "hello.py").write_text("import sys\nprint('hello', *sys.argv[1:])\n", encoding="utf-8")
    backend_plugin = NativeSkillPlugin(base_dir=tmp_path)
    backend_plugin.discover(tmp_path)

    ctx = _FakeCtx(reg)
    ctx.skill_backend_plugin = backend_plugin

    plugin = RunSkillScriptPlugin()
    result = plugin.on_execute_tool(
        "run_skill_script",
        {"skill": "my-skill", "script": "hello.py", "args": ["via-backend"]},
        ctx=ctx,
    )

    assert result["ok"] is True
    assert result["exit_code"] == 0
    assert "hello" in result["stdout"]
    assert "via-backend" in result["stdout"]


def test_run_script_via_injected_backend_plugin_wraps_exceptions(tmp_path: Path):
    """If the backend plugin raises (e.g. skill not discovered, bad config),
    the tool must return a structured error, not propagate the exception."""
    reg = _registry_with_scripts(tmp_path)

    class _BrokenBackend:
        def run_script(self, **_: Any) -> dict[str, Any]:
            raise RuntimeError("backend exploded")

    ctx = _FakeCtx(reg)
    ctx.skill_backend_plugin = _BrokenBackend()

    plugin = RunSkillScriptPlugin()
    result = plugin.on_execute_tool(
        "run_skill_script",
        {"skill": "my-skill", "script": "hello.py"},
        ctx=ctx,
    )

    assert "error" in result
    assert "backend exploded" in result["error"]


# ---------------------------------------------------------------------------
# Sandbox helpers — now in skill-sandbox package
# ---------------------------------------------------------------------------


def test_build_safe_env_strips_secrets():
    import os

    from sandbox.runner import _build_safe_env

    os.environ["MY_APIKEY"] = "secret123"
    env = _build_safe_env(Path("/tmp"), {})
    assert "MY_APIKEY" not in env
    del os.environ["MY_APIKEY"]


def test_build_safe_env_includes_pwd():
    from sandbox.runner import _build_safe_env

    env = _build_safe_env(Path("/my/skill/dir"), {})
    assert env["PWD"] == "/my/skill/dir"


def test_build_safe_env_merges_extra():
    from sandbox.runner import _build_safe_env

    env = _build_safe_env(Path("/tmp"), {"MY_CONF": "value"})
    assert env["MY_CONF"] == "value"


def test_posix_set_limits_is_callable():
    from sandbox.runner import _posix_set_limits

    # Should not raise even on platforms without resource module
    _posix_set_limits()
