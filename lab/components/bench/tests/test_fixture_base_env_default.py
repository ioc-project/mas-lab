"""A batch started without the CLI's environment gives fixtures the process environment."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from mas.lab.benchmark.schedule.run_batch import prepare as prep


class _Stop(Exception):
    pass


def _registry_env(monkeypatch, tmp_path: Path, **kwargs):
    seen: dict = {}

    class _Reg:
        def __init__(self, **kw):
            seen.update(kw)
            raise _Stop

    monkeypatch.setattr("mas.lab.benchmark.environment.fixtures.FixtureRegistry", _Reg)
    monkeypatch.setattr("mas.lab.benchmark.environment.fixtures.reap_stale_fixtures", lambda _d: [])
    monkeypatch.setattr(prep, "setup_output_dir", lambda *_a, **_k: (tmp_path, tmp_path / "r.csv", {}))

    class _Loaded:
        experiment_yaml = tmp_path / "experiment.yaml"

    with pytest.raises(_Stop):
        asyncio.run(prep.prepare_batch(_Loaded(), output_dir=tmp_path, **kwargs))
    return seen["base_env"]


def test_missing_base_env_falls_back_to_process_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("FIXTURE_ENV_PROBE", "yes")
    assert _registry_env(monkeypatch, tmp_path)["FIXTURE_ENV_PROBE"] == "yes"


def test_given_base_env_is_used_as_is(monkeypatch, tmp_path):
    monkeypatch.setenv("FIXTURE_ENV_PROBE", "yes")
    assert _registry_env(monkeypatch, tmp_path, base_env={"A": "1"}) == {"A": "1"}
