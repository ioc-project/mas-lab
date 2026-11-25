#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest
from mas.ctl.session.llm_scheduler_env import install_env_overrides, llm_scheduler_overrides
from mas.library.standard.lib.llm_scheduler import SchedulerPolicy, explicit_overrides, install_overrides


@pytest.fixture(autouse=True)
def _clear():
    install_overrides(None)
    yield
    install_overrides(None)


def test_empty_environment_gives_no_override() -> None:
    assert llm_scheduler_overrides({}) == {}
    assert llm_scheduler_overrides({"MAS_LLM_RPM": "  ", "MAS_LLM_SCHEDULER_USER": ""}) == {}


def test_variables_map_to_override_keys() -> None:
    got = llm_scheduler_overrides(
        {
            "MAS_LLM_RPM": "12.5",
            "MAS_LLM_MAX_IN_FLIGHT": "4",
            "MAS_LLM_SCHEDULER_USER": "lab",
            "MAS_LLM_SCHEDULER_PROVIDER": "vllm",
            "MAS_LLM_SCHEDULER_MODEL": "gemma",
            "MAS_LLM_SCHEDULER_STATE": "/tmp/s.json",
        }
    )
    assert got == {
        "requests_per_minute": 12.5,
        "max_in_flight": 4,
        "user": "lab",
        "provider": "vllm",
        "model": "gemma",
        "state_path": "/tmp/s.json",
    }


def test_install_reaches_the_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAS_LLM_MAX_IN_FLIGHT", "2")
    monkeypatch.setenv("MAS_LLM_RPM", "10")
    install_env_overrides()
    assert explicit_overrides() == {"max_in_flight": 2, "requests_per_minute": 10.0}
    pol = SchedulerPolicy.from_params({"max_in_flight": 8, "rpm": 1})
    assert (pol.max_in_flight, pol.requests_per_minute) == (2, 10.0)


def test_mas_ctl_entry_installs_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    from click.testing import CliRunner
    from mas.ctl.cli.main import app

    monkeypatch.setenv("MAS_LLM_MAX_IN_FLIGHT", "5")
    CliRunner().invoke(app, ["--help"])
    # --help exits before the group body; invoke a real subcommand instead
    CliRunner().invoke(app, ["infra", "show", "--help"])
    assert explicit_overrides().get("max_in_flight") == 5


def test_cache_path_and_infra_refs_binding(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mas.ctl.workspace.config import WorkspaceConfig, bind_infra_refs
    from mas.runtime.engine import llm_cache

    before = llm_cache.snapshot_paths()
    try:
        install_env_overrides({"MAS_LLM_CACHE": str(tmp_path / "c.json")})
        assert llm_cache.snapshot_paths()["cli"] == str(tmp_path / "c.json")
    finally:
        llm_cache._PATHS.update(before)
    monkeypatch.setenv("MAS_INFRA_REFS", "env:one")
    ws = WorkspaceConfig.load(tmp_path)
    assert ws.effective_infra_refs == ["env:one"]
    with bind_infra_refs(["run:a"]):
        assert ws.effective_infra_refs == ["run:a"]
    with bind_infra_refs([]):
        assert ws.effective_infra_refs == ws.infra_refs  # the CLI variable is ignored
    assert ws.effective_infra_refs == ["env:one"]


def _forget(*names: str) -> None:
    """Drop variables a ``.env`` added. Not ``monkeypatch.delenv``: it would restore them at teardown."""
    import os

    for name in names:
        os.environ.pop(name, None)


def test_a_dotenv_loaded_after_startup_still_sets_the_scheduler(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A manifest's .env overlays non-secret MAS_LLM_* keys into hostenv, never os.environ."""
    from mas.ctl.env import load_dotenv
    from mas.runtime import hostenv

    monkeypatch.delenv("MAS_LLM_MAX_IN_FLIGHT", raising=False)
    install_env_overrides()
    assert "max_in_flight" not in explicit_overrides()
    env_file = tmp_path / ".env"
    env_file.write_text("MAS_LLM_MAX_IN_FLIGHT=5\n", encoding="utf-8")
    try:
        assert load_dotenv(explicit=env_file) is not None
        assert explicit_overrides()["max_in_flight"] == 5
    finally:
        hostenv.clear_overlay(("MAS_LLM_MAX_IN_FLIGHT",))
        _forget("MAS_LLM_MAX_IN_FLIGHT", "MAS_DOTENV_LOADED")


def test_a_dotenv_read_mid_run_keeps_the_overrides_a_host_handed_over(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from mas.ctl.env import load_dotenv
    from mas.runtime import hostenv

    monkeypatch.delenv("MAS_LLM_RPM", raising=False)
    install_overrides({"max_in_flight": 9})  # what a spawned run worker was given
    env_file = tmp_path / ".env"
    env_file.write_text("MAS_LLM_RPM=30\n", encoding="utf-8")
    try:
        load_dotenv(explicit=env_file)
        assert explicit_overrides() == {"max_in_flight": 9, "requests_per_minute": 30.0}
    finally:
        hostenv.clear_overlay(("MAS_LLM_RPM",))
        _forget("MAS_LLM_RPM", "MAS_DOTENV_LOADED")
