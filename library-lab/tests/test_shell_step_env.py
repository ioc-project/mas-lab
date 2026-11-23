#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A shell step's child gets the allowlist plus what the step declares, nothing else."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from mas.library.lab.steps.services.shell import ShellStep
from mas.runtime import hostenv
from mas.runtime.secrets import SecretRefError
from secret_fakes import bound_secrets


def _run(tmp_path, command: str, **config):
    out = tmp_path / "seen.txt"
    step = ShellStep(name="s", config={"command": f"{command} > {out}", "cwd": str(tmp_path), **config})
    asyncio.run(step.execute(SimpleNamespace()))
    return out.read_text()


def test_undeclared_overlay_and_host_secrets_stay_out(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-CANARY")
    with hostenv.overlay({"SVC_ENDPOINT": "http://svc"}):
        assert _run(tmp_path, 'printf %s "$SVC_ENDPOINT:$OPENAI_API_KEY"') == ":"


def test_passthrough_copies_a_named_service_export(tmp_path):
    with hostenv.overlay({"SVC_ENDPOINT": "http://svc", "OTHER": "x"}):
        seen = _run(tmp_path, 'printf %s "$SVC_ENDPOINT:$OTHER"', passthrough=["SVC_ENDPOINT"])
    assert seen == "http://svc:"


def test_passthrough_never_copies_a_host_value_no_service_exported(tmp_path, monkeypatch):
    # A host variable named in passthrough is not copied: only a service's export is.
    monkeypatch.setenv("HOST_ONLY_X", "/host-only")
    assert _run(tmp_path, 'printf %s "$HOST_ONLY_X"', passthrough=["HOST_ONLY_X"]) == ""


def test_declared_secret_reaches_only_this_child(tmp_path):
    with bound_secrets({"tool-token": "tok-CANARY"}):
        seen = _run(tmp_path, 'printf %s "$TOKEN:$MODE"', env={"TOKEN": {"secret": "tool-token"}, "MODE": "fast"})
    assert seen == "tok-CANARY:fast"
    assert hostenv.get("TOKEN") is None


def test_env_string_reference_is_refused(tmp_path):
    with pytest.raises(SecretRefError, match="env: prefix is removed"):
        _run(tmp_path, "true", env={"TOKEN": "env:OPENAI_API_KEY"})


@pytest.mark.parametrize(
    ("name", "value"),
    (("HTTPS_PROXY", "http://user:pw@proxy:3128"), ("OPENAI_API_KEY", "sk-literal"), ("MAS_SECRET_X", "sk-literal")),
)
def test_literal_secret_in_env_is_refused_and_nothing_runs(tmp_path, name, value):
    with pytest.raises(SecretRefError) as info:
        _run(tmp_path, "true", env={name: value})
    assert value not in str(info.value)
    assert not (tmp_path / "seen.txt").exists()
