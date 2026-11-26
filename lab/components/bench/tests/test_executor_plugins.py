#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Executor plugins: new spelling, old schedule keys, third-party libraries."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.lab.benchmark.execution.retry import run_with_retrier
from mas.lab.benchmark.execution.roles import resolve_executor, resolve_role_plugin
from mas.lab.deprecations import clear_deprecation_warnings
from mas.lab.lab.config import ExperimentExecutorSpec, ExperimentScheduleSpec, MASExperimentConfig


def _load(tmp_path: Path, body: str) -> MASExperimentConfig:
    path = tmp_path / "experiment.yaml"
    path.write_text(
        "experiment:\n"
        "  name: executor-test\n"
        "  application:\n"
        "    manifest: ./mas.yaml\n"
        "  scenarios:\n"
        "    - id: s1\n"
        f"{body}",
        encoding="utf-8",
    )
    return MASExperimentConfig.from_yaml(path)


@pytest.fixture(autouse=True)
def _clear_deprecations() -> None:
    clear_deprecation_warnings()


def test_omitted_executor_defaults_to_native(tmp_path: Path) -> None:
    cfg = _load(tmp_path, "  run:\n    n_runs: 1\n")
    assert cfg.executor.plugin == "native"
    assert cfg.retry.plugin == "native"
    assert cfg.retry.params == {}
    assert cfg.executor.timeout == 300
    assert cfg.executor.worker_processes == 1


def test_old_schedule_timeout_lands_on_executor(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    with caplog.at_level(logging.WARNING, logger="mas.lab.deprecations"):
        cfg = _load(
            tmp_path,
            "  run:\n    n_runs: 1\n  schedule:\n    timeout: 12\n    max_attempts: 1\n",
        )
    assert "experiment.schedule.executor" in caplog.text
    assert cfg.executor.timeout == 12
    assert cfg.retry.params == {"max_attempts": 1}
    assert not hasattr(cfg.schedule, "timeout")


def test_new_executor_block_does_not_warn(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    with caplog.at_level(logging.WARNING, logger="mas.lab.deprecations"):
        cfg = _load(
            tmp_path,
            "  run:\n    n_runs: 1\n  executor:\n    plugin: native\n    timeout: 9\n",
        )
    assert "experiment.schedule.executor" not in caplog.text
    assert cfg.executor.timeout == 9


def test_mixing_schedule_timeout_and_executor_timeout_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not both"):
        _load(
            tmp_path,
            "  schedule:\n    timeout: 12\n  executor:\n    timeout: 9\n",
        )


def test_params_and_top_level_timeout_is_an_error() -> None:
    with pytest.raises(ValueError, match="not both"):
        ExperimentExecutorSpec.from_dict({"timeout": 9, "params": {"timeout": 12}})


def test_plan_yaml_params_lift_to_host_keys() -> None:
    spec = ExperimentExecutorSpec.from_dict({"plugin": "native", "params": {"timeout": 42}})
    assert spec.timeout == 42
    assert spec.params == {}


def test_executor_refuses_retry_keys() -> None:
    with pytest.raises(ValueError, match="unknown keys"):
        ExperimentExecutorSpec.from_dict({"max_attempts": 2})


def test_schedule_from_dict_refuses_executor_keys() -> None:
    with pytest.raises(ValueError, match="experiment.executor"):
        ExperimentScheduleSpec.from_dict({"plugin": "coverage", "timeout": 12})


def test_unknown_executor_names_the_registered_list() -> None:
    with pytest.raises(ValueError, match=r"unknown executor 'remote'; registered:"):
        ExperimentExecutorSpec.from_dict({"plugin": "remote"})


def _flaky(fail_times: int):
    calls = {"n": 0}

    def run() -> dict:
        calls["n"] += 1
        if calls["n"] <= fail_times:
            return {"status": "error", "content": "LLM request failed: HTTP 503"}
        return {"status": "ok", "content": "done"}

    return run, calls


@pytest.mark.asyncio
async def test_native_executor_runs_once_and_never_retries() -> None:
    run, calls = _flaky(5)
    done = await resolve_executor("native").attempt(run)
    assert done.status == "error"
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_native_retrier_joined_to_executor_retries_until_ok() -> None:
    run, calls = _flaky(1)
    retrier = resolve_role_plugin("retrier", "native")
    retrier.validate_params({"max_attempts": 3, "backoff_s": 0})
    out = await run_with_retrier(resolve_executor("native"), retrier, run)
    assert out.status == "ok"
    assert out.attempts == 2


@pytest.mark.asyncio
async def test_never_retrier_from_test_library_stops_after_one(role_plugins_lib) -> None:
    run, calls = _flaky(5)
    out = await run_with_retrier(resolve_executor("native"), resolve_role_plugin("retrier", "never"), run)
    assert out.status == "error"
    assert out.attempts == 1
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_attempts_before_counts_against_the_cap() -> None:
    run, calls = _flaky(5)
    retrier = resolve_role_plugin("retrier", "native")
    retrier.validate_params({"max_attempts": 3, "backoff_s": 0})
    out = await run_with_retrier(resolve_executor("native"), retrier, run, attempts_before=2)
    assert calls["n"] == 1
    assert out.attempts == 3


def test_timeout_below_one_is_an_error() -> None:
    with pytest.raises(ValueError, match="executor.timeout"):
        ExperimentExecutorSpec.from_dict({"timeout": 0})
    with pytest.raises(ValueError, match="executor.timeout"):
        ExperimentExecutorSpec.from_dict({"timeout": True})


def test_execution_timeout_and_executor_timeout_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not both"):
        _load(
            tmp_path,
            "  execution:\n    timeout: 12\n  executor:\n    timeout: 9\n",
        )


def test_execution_timeout_and_schedule_timeout_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not both"):
        _load(
            tmp_path,
            "  execution:\n    timeout: 12\n  schedule:\n    timeout: 9\n",
        )
