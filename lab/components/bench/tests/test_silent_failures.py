#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Configuration mistakes must fail loudly instead of running something else."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


def test_run_hash_changes_with_infra():
    from mas.lab.benchmark.cache.trace_store import compute_run_hash

    args = ({"agents": []}, {"inputs": {"user": "Q"}}, "i", 0, {})
    a = compute_run_hash(*args, infra_info={"llm_proxy": {"mappings": {"m": "a"}}})
    b = compute_run_hash(*args, infra_info={"llm_proxy": {"mappings": {"m": "b"}}})
    assert a != b


def test_dataset_is_required(tmp_path: Path):
    from mas.lab.benchmark.schedule.run_batch.load import _load_dataset_items

    with pytest.raises(ValueError, match="no dataset"):
        _load_dataset_items(SimpleNamespace(dataset=None))
    with pytest.raises(FileNotFoundError):
        _load_dataset_items(SimpleNamespace(dataset=str(tmp_path / "missing.yaml")))


def test_missing_scenario_config_is_an_error(tmp_path: Path):
    from mas.lab.benchmark.schedule.run_batch.prepare import preload_scenario_configs

    scenario = SimpleNamespace(overlays=SimpleNamespace(flattened=lambda: ["baseline"]))
    exp = SimpleNamespace(
        get_scenario=lambda _sid: scenario,
        mas=SimpleNamespace(manifest=tmp_path / "missing-mas.yaml"),
    )
    loaded = SimpleNamespace(exp=exp, configs_dir=None, experiment_yaml=tmp_path / "e.yaml", scenario_ids=["baseline"])
    with pytest.raises(FileNotFoundError, match="scenario 'baseline'"):
        preload_scenario_configs(loaded)


def test_service_env_conflict_between_benchmarks_is_an_error(tmp_path: Path, monkeypatch):
    from mas.lab.benchmark.service_manager import ServiceDef, ServiceManager

    monkeypatch.delenv("MAS_TEST_SVC_ENDPOINT", raising=False)
    first = ServiceManager(tmp_path / "none.yaml")
    second = ServiceManager(tmp_path / "none.yaml")
    a = ServiceDef(name="a", env={"MAS_TEST_SVC_ENDPOINT": "http://a"})
    b = ServiceDef(name="b", env={"MAS_TEST_SVC_ENDPOINT": "http://b"})
    same = ServiceDef(name="c", env={"MAS_TEST_SVC_ENDPOINT": "http://a"})

    first._apply_env(a)
    second._apply_env(same)
    with pytest.raises(RuntimeError, match="conflicts"):
        second._apply_env(b)
    first._restore_env(a)
    import os

    assert os.environ["MAS_TEST_SVC_ENDPOINT"] == "http://a"
    second._restore_env(same)
    assert "MAS_TEST_SVC_ENDPOINT" not in os.environ


def test_service_env_unknown_placeholder_is_an_error(tmp_path: Path):
    from mas.lab.benchmark.service_manager import ServiceDef, ServiceManager

    mgr = ServiceManager(tmp_path / "none.yaml", template_vars={"output_dir": "/o"})
    with pytest.raises(ValueError, match="unknown placeholder"):
        mgr._resolve_env(ServiceDef(name="x", env={"K": "{nope}/x"}))
