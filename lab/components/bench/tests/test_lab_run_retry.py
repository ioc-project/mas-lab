#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Lab-level reattempt of failed scenario×item×run executions."""

from __future__ import annotations

import json

import pytest
from mas.lab.benchmark.execution.retry import lab_run_error_is_retryable, run_with_retrier
from mas.lab.benchmark.execution.roles import resolve_executor, resolve_role_plugin
from mas.lab.lab.config.execution import ExperimentRetrySpec


def _retrier(**params):
    retrier = resolve_role_plugin("retrier", "native")
    retrier.validate_params(params)
    return retrier


def test_lab_run_retry_defaults() -> None:
    spec = ExperimentRetrySpec.from_dict(None)
    assert (spec.plugin, spec.params) == ("native", {})
    retrier = _retrier()
    assert (retrier.max_attempts, retrier.backoff_s) == (3, 2.0)


def test_lab_run_error_is_retryable_for_llm_and_not_auth() -> None:
    assert lab_run_error_is_retryable("LLM request failed: HTTP 500 from LLM provider", status="error")
    assert lab_run_error_is_retryable("Connection refused", status="error")
    assert not lab_run_error_is_retryable("LLM request failed: HTTP 401 (authentication/authorization)", status="error")
    assert not lab_run_error_is_retryable("TLS certificate verification failed", status="error")
    assert not lab_run_error_is_retryable("ok", status="ok")


@pytest.mark.asyncio
async def test_retries_then_succeeds_and_reports_each_retry() -> None:
    calls = {"n": 0}

    def flaky() -> dict:
        calls["n"] += 1
        if calls["n"] < 3:
            return {"status": "error", "content": "LLM request failed: HTTP 503"}
        return {"status": "ok", "content": "done", "session_id": "s1"}

    seen: list[int] = []
    slept: list[float] = []

    async def sleep(delay: float) -> None:
        slept.append(delay)

    out = await run_with_retrier(
        resolve_executor("native"),
        _retrier(max_attempts=3, backoff_s=1),
        flaky,
        on_retry=lambda n, _e: seen.append(n),
        sleep=sleep,
    )
    assert (out.status, out.output, out.attempts) == ("ok", "done", 3)
    assert out.result["session_id"] == "s1"
    assert seen == [1, 2]
    assert slept == [1.0, 2.0]


@pytest.mark.asyncio
async def test_hidden_failure_is_not_retried_when_fatal() -> None:
    calls = {"n": 0}

    def fatal() -> dict:
        calls["n"] += 1
        return {"status": "ok", "content": "LLM request failed: HTTP 401 (authentication/authorization). Check key"}

    out = await run_with_retrier(resolve_executor("native"), _retrier(max_attempts=3, backoff_s=0), fatal)
    assert out.status == "error" and out.attempts == 1 and calls["n"] == 1


@pytest.mark.asyncio
async def test_exception_is_retried() -> None:
    calls = {"n": 0}

    def boom() -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("Connection refused")
        return {"status": "ok", "content": "recovered"}

    out = await run_with_retrier(resolve_executor("native"), _retrier(max_attempts=3, backoff_s=0), boom)
    assert (out.status, out.output, out.attempts) == ("ok", "recovered", 2)


@pytest.mark.asyncio
async def test_failing_before_attempt_is_final() -> None:
    async def bad() -> None:
        raise RuntimeError("pre step failed")

    out = await run_with_retrier(
        resolve_executor("native"), _retrier(max_attempts=3, backoff_s=0), lambda: {"status": "ok"}, before_attempt=bad
    )
    assert out.status == "error" and out.attempts == 1 and "pre step failed" in out.error


def test_write_run_result_replaces_error_cache_and_records_attempts(tmp_path) -> None:
    from mas.lab.benchmark.cache.trace_store import write_run_result

    write_run_result(tmp_path, "error", 10.0, "HTTP 503", attempts=3)
    assert json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))["attempts"] == 3
    write_run_result(tmp_path, "ok", 20.0, "", attempts=4)
    data = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert (data["status"], data["error"], data["attempts"]) == ("ok", "", 4)
