#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Lab-level reattempt of failed scenario×item×run executions."""

from __future__ import annotations

import json

import pytest
from mas.lab.benchmark.execution.retry import lab_run_error_is_retryable, run_with_retrier, runner_outcome
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


def test_a_peer_failure_inside_collated_content_fails_the_run() -> None:
    """A fan-out caller returns its peers' replies in its own text: a failed peer must not make the run ok."""
    llm_403 = "=== 1 ===\nLLM request failed: HTTP 403. Check the SecretRef on api_key\n\n=== 2 ===\n408"
    llm_503 = "=== 1 ===\nLLM request failed: HTTP 503\n\n=== 2 ===\n408"
    send_runtime = "=== 1 ===\n[send] agent 'worker-1' failed: a sync call cannot wait for async work"
    send_via = "=== 1 ===\n[send] agent 'worker-1' via local-group failed: link down\n\n=== 2 ===\n408"
    send_bus = "=== 1 ===\n[send] agent 'worker-1' not available on bus\n\n=== 2 ===\n408"
    ok = "=== 1 ===\n408\n\n=== 2 ===\n408"
    # A caller mistake is not a peer failure; the run stays ok so the model can recover.
    send_intent = "=== 1 ===\n[send] unknown intent 'nope'\n\n=== 2 ===\n408"
    # A replay cache miss on a specialist follow-up: the peer never ran; the caller continues.
    send_miss = (
        "=== 1 ===\n"
        "[send] agent 'itinerary_agent' failed: agent 'itinerary_agent' turn failed: "
        "llm_cache miss (raise_on_miss=true) for key abc\n\n=== 2 ===\n408"
    )

    assert runner_outcome({"status": "ok", "content": llm_403})[0] == "error"
    assert runner_outcome({"status": "ok", "content": send_runtime})[0] == "error"
    assert runner_outcome({"status": "ok", "content": send_via})[0] == "error"
    assert runner_outcome({"status": "ok", "content": send_bus})[0] == "error"
    assert runner_outcome({"status": "ok", "content": ok})[0] == "ok"
    assert runner_outcome({"status": "ok", "content": send_intent})[0] == "ok"
    assert runner_outcome({"status": "ok", "content": send_miss})[0] == "ok"
    assert not lab_run_error_is_retryable(llm_403, status="error")  # a 403 is an authorization failure: no retry
    assert lab_run_error_is_retryable(llm_503, status="error")
    assert lab_run_error_is_retryable(send_via, status="error")


@pytest.mark.asyncio
async def test_collated_peer_failure_fails_the_run_through_the_executor() -> None:
    """The native executor, not only runner_outcome, must see a buried peer failure."""
    via = "=== 1 ===\n[send] agent 'worker-1' via local-group failed: link down\n\n=== 2 ===\n408"
    llm_403 = "=== 1 ===\nLLM request failed: HTTP 403. Check the SecretRef on api_key\n\n=== 2 ===\n408"
    llm_503 = "=== 1 ===\nLLM request failed: HTTP 503\n\n=== 2 ===\n408"

    calls = {"n": 0}

    def once_via() -> dict:
        calls["n"] += 1
        return {"status": "ok", "content": via}

    out = await run_with_retrier(resolve_executor("native"), _retrier(max_attempts=3, backoff_s=0), once_via)
    assert out.status == "error" and calls["n"] == 3  # link-down is retryable

    calls["n"] = 0

    def once_403() -> dict:
        calls["n"] += 1
        return {"status": "ok", "content": llm_403}

    out = await run_with_retrier(resolve_executor("native"), _retrier(max_attempts=3, backoff_s=0), once_403)
    assert out.status == "error" and calls["n"] == 1

    calls["n"] = 0

    def flaky_503() -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            return {"status": "ok", "content": llm_503}
        return {"status": "ok", "content": "done"}

    out = await run_with_retrier(resolve_executor("native"), _retrier(max_attempts=3, backoff_s=0), flaky_503)
    assert (out.status, out.output, out.attempts, calls["n"]) == ("ok", "done", 2, 2)

    calls["n"] = 0
    miss = (
        "[send] agent 'itinerary_agent' failed: agent 'itinerary_agent' turn failed: "
        "llm_cache miss (raise_on_miss=true) for key abc"
    )

    def once_miss() -> dict:
        calls["n"] += 1
        return {"status": "ok", "content": f"plan\n{miss}\n408"}

    out = await run_with_retrier(resolve_executor("native"), _retrier(max_attempts=3, backoff_s=0), once_miss)
    assert (out.status, out.attempts, calls["n"]) == ("ok", 1, 1)
