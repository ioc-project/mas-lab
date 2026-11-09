#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Lab-level reattempt of failed scenario×item×run executions."""

from __future__ import annotations

import pytest
from mas.lab.benchmark.schedule.run_batch.execute import (
    invoke_lab_run_with_retry,
    lab_run_error_is_retryable,
    lab_run_retry_settings,
)
from mas.lab.lab.config.execution import MASExecutionSpec


def test_lab_run_retry_defaults() -> None:
    attempts, backoff = lab_run_retry_settings(None)
    assert attempts == 3
    assert backoff == 2.0
    spec = MASExecutionSpec.from_dict({})
    assert spec.max_attempts == 3
    assert spec.retry_backoff_s == 2.0
    spec = MASExecutionSpec.from_dict({"max_attempts": 1, "retry_backoff_s": 0})
    assert spec.max_attempts == 1
    assert spec.retry_backoff_s == 0.0


def test_lab_run_error_is_retryable_for_llm_and_not_auth() -> None:
    assert lab_run_error_is_retryable("LLM request failed: HTTP 500 from LLM provider", status="error")
    assert lab_run_error_is_retryable("Connection refused", status="error")
    assert not lab_run_error_is_retryable("LLM request failed: HTTP 401 (authentication/authorization)", status="error")
    assert not lab_run_error_is_retryable("TLS certificate verification failed", status="error")
    assert not lab_run_error_is_retryable("ok", status="ok")


@pytest.mark.asyncio
async def test_invoke_lab_run_retries_then_succeeds() -> None:
    calls = {"n": 0}

    def flaky() -> dict:
        calls["n"] += 1
        if calls["n"] < 3:
            return {"status": "error", "content": "LLM request failed: HTTP 503"}
        return {"status": "ok", "content": "done", "session_id": "s1"}

    retries: list[int] = []
    result, status, error, output, used = await invoke_lab_run_with_retry(
        flaky,
        max_attempts=3,
        backoff_s=0,
        on_retry=lambda failed, _n, _e: retries.append(failed),
    )
    assert status == "ok"
    assert output == "done"
    assert result["session_id"] == "s1"
    assert used == 3
    assert retries == [1, 2]
    assert not error


@pytest.mark.asyncio
async def test_invoke_lab_run_does_not_retry_fatal() -> None:
    calls = {"n": 0}

    def fatal() -> dict:
        calls["n"] += 1
        return {
            "status": "ok",
            "content": "LLM request failed: HTTP 401 (authentication/authorization). Check the API key",
        }

    _, status, error, _output, used = await invoke_lab_run_with_retry(fatal, max_attempts=3, backoff_s=0)
    assert status == "error"
    assert used == 1
    assert calls["n"] == 1
    assert "401" in error


@pytest.mark.asyncio
async def test_invoke_lab_run_retries_exceptions() -> None:
    calls = {"n": 0}

    def boom() -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("Connection refused")
        return {"status": "ok", "content": "recovered"}

    _, status, error, output, used = await invoke_lab_run_with_retry(boom, max_attempts=3, backoff_s=0)
    assert status == "ok"
    assert output == "recovered"
    assert used == 2
    assert not error


def test_write_run_result_replaces_error_cache(tmp_path) -> None:
    from mas.lab.benchmark.cache.trace_store import write_run_result

    write_run_result(tmp_path, "error", 10.0, "HTTP 503")
    write_run_result(tmp_path, "ok", 20.0, "")
    import json

    data = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert data["status"] == "ok"
    assert data["error"] == ""


def test_write_run_result_preserves_successful_cache(tmp_path) -> None:
    from mas.lab.benchmark.cache.trace_store import write_run_result

    write_run_result(tmp_path, "ok", 10.0, "")
    write_run_result(tmp_path, "error", 20.0, "later fail")
    import json

    data = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert data["status"] == "ok"
