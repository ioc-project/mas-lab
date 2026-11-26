#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Child processes do not inherit host secrets."""

from __future__ import annotations

import subprocess
import sys

from mas.runtime.secrets.process import child_env
from secret_fakes import CANARY


def test_spawned_child_does_not_see_canary(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", CANARY)
    monkeypatch.setenv("UNRELATED_HOST", "keep-me-out")
    code = "import os, json; print(json.dumps(dict(os.environ)))"
    env = child_env({"DECLARED": "ok"})
    proc = subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True, env=env)
    assert env["DECLARED"] == "ok" and "UNRELATED_HOST" not in env
    assert CANARY not in proc.stdout + proc.stderr and "OPENAI_API_KEY" not in proc.stdout


def test_lab_child_env_declares_xdg(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", CANARY)
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/mas-xdg")
    monkeypatch.setenv("MAS_WORKSPACE_ROOT", "/tmp/mas-ws")
    env = child_env({"MAS_CONTROLLER_PORT": "9"}, lab=True)
    assert env["XDG_CONFIG_HOME"] == "/tmp/mas-xdg"
    assert env["MAS_WORKSPACE_ROOT"] == "/tmp/mas-ws"
    assert env["MAS_CONTROLLER_PORT"] == "9"
    assert "OPENAI_API_KEY" not in env


def test_a_library_declares_its_own_host_keys_for_lab_children(monkeypatch) -> None:
    """A key no runtime list names reaches a lab child only after its owner declares it, and never a plain child."""
    from mas.runtime import envkeys

    monkeypatch.setattr(envkeys, "_DECLARED_BY_LIBRARIES", set())
    monkeypatch.setenv("MAS_LLM_RPM", "30")
    assert "MAS_LLM_RPM" not in child_env(lab=True)
    envkeys.declare_lab_keys(["MAS_LLM_RPM"])
    assert envkeys.declared_lab_keys() == {"MAS_LLM_RPM"}
    assert child_env(lab=True)["MAS_LLM_RPM"] == "30"
    assert "MAS_LLM_RPM" not in child_env()
