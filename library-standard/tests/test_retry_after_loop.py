#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Retry loop honours server Retry-After hints (sync and async), offline with a fake clock."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
import pytest
from mas.library.standard.lib.retry import RetryPolicy, call_with_retry
from mas.library.standard.lib.retry.loop import acall_with_retry
from mas.runtime.reliability.classes import ClassifiedFailure, FailureClass
from mas.runtime.reliability.classify import classify_llm_failure
from mas.runtime.spec.gov import SpecBindingError

_LOOP = "mas.library.standard.lib.retry.loop"


def _transient(retry_after_s: float | None) -> ClassifiedFailure:
    return ClassifiedFailure(
        "throttled", failure_class=FailureClass.TRANSIENT, code="HTTP_429", retry_after_s=retry_after_s
    )


def _flaky(failures: list[ClassifiedFailure]) -> Any:
    queue = list(failures)

    def call() -> str:
        if queue:
            raise queue.pop(0)
        return "ok"

    return call


def _async_flaky(failures: list[ClassifiedFailure]) -> Any:
    queue = list(failures)

    async def call() -> str:
        if queue:
            raise queue.pop(0)
        return "ok"

    return call


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleeps: list[float] = []
    monkeypatch.setattr(f"{_LOOP}.time.sleep", lambda seconds: sleeps.append(seconds))

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(f"{_LOOP}.asyncio.sleep", fake_sleep)
    return sleeps


def _identity(exc: BaseException) -> ClassifiedFailure:
    assert isinstance(exc, ClassifiedFailure)
    return exc


def test_sync_loop_sleeps_for_server_delay(clock: list[float]) -> None:
    policy = RetryPolicy(max_attempts=3, backoff_s=0.5, jitter=False)
    result = call_with_retry(_flaky([_transient(3.0)]), policy=policy, classify=_identity)
    assert result == "ok"
    assert clock == [3.0]


def test_sync_loop_server_delay_never_shortens_backoff(clock: list[float]) -> None:
    policy = RetryPolicy(max_attempts=3, backoff_s=5.0, jitter=False)
    call_with_retry(_flaky([_transient(1.0)]), policy=policy, classify=_identity)
    assert clock == [5.0]


def test_sync_loop_honours_a_hint_at_the_cap(clock: list[float]) -> None:
    policy = RetryPolicy(max_attempts=3, backoff_s=0.5, jitter=False, max_retry_after_s=10.0)
    call_with_retry(_flaky([_transient(10.0)]), policy=policy, classify=_identity)
    assert clock == [10.0]


def test_sync_loop_does_not_retry_when_the_server_asks_for_longer_than_the_cap(clock: list[float]) -> None:
    policy = RetryPolicy(max_attempts=3, backoff_s=0.5, jitter=False, max_retry_after_s=10.0)
    calls = _flaky([_transient(500.0)])
    with pytest.raises(ClassifiedFailure) as raised:
        call_with_retry(calls, policy=policy, classify=_identity)
    assert raised.value.retry_after_s == 500.0
    assert clock == []


def test_sync_loop_default_cap_is_sixty_seconds(clock: list[float]) -> None:
    policy = RetryPolicy(max_attempts=3, backoff_s=0.5, jitter=False)
    assert policy.honor_retry_after is True
    assert policy.max_retry_after_s == 60.0
    call_with_retry(_flaky([_transient(60.0)]), policy=policy, classify=_identity)
    assert clock == [60.0]
    with pytest.raises(ClassifiedFailure):
        call_with_retry(_flaky([_transient(600.0)]), policy=policy, classify=_identity)


def test_sync_loop_ignores_hint_when_disabled(clock: list[float]) -> None:
    policy = RetryPolicy(max_attempts=3, backoff_s=0.5, jitter=False, honor_retry_after=False)
    call_with_retry(_flaky([_transient(30.0)]), policy=policy, classify=_identity)
    assert clock == [0.5]


def test_sync_loop_without_hint_uses_backoff(clock: list[float]) -> None:
    policy = RetryPolicy(max_attempts=3, backoff_s=0.5, backoff_multiplier=2.0, jitter=False)
    call_with_retry(_flaky([_transient(None), _transient(None)]), policy=policy, classify=_identity)
    assert clock == [0.5, 1.0]


def test_sync_loop_logs_server_delay(clock: list[float], caplog: pytest.LogCaptureFixture) -> None:
    policy = RetryPolicy(max_attempts=2, backoff_s=0.5, jitter=False)
    with caplog.at_level(logging.WARNING, logger="mas.runtime.reliability"):
        call_with_retry(_flaky([_transient(2.0)]), policy=policy, classify=_identity, target="llm")
    assert "server Retry-After 2.000s" in caplog.text


def test_async_loop_sleeps_for_server_delay(clock: list[float]) -> None:
    policy = RetryPolicy(max_attempts=3, backoff_s=0.5, jitter=False)

    result = asyncio.run(acall_with_retry(_async_flaky([_transient(3.0)]), policy=policy, classify=_identity))
    assert result == "ok"
    assert clock == [3.0]


def test_async_loop_caps_and_ignores_when_disabled(clock: list[float]) -> None:
    capped = RetryPolicy(max_attempts=3, backoff_s=0.5, jitter=False, max_retry_after_s=4.0)
    asyncio.run(acall_with_retry(_async_flaky([_transient(4.0)]), policy=capped, classify=_identity))
    with pytest.raises(ClassifiedFailure):
        asyncio.run(acall_with_retry(_async_flaky([_transient(99.0)]), policy=capped, classify=_identity))
    off = RetryPolicy(max_attempts=3, backoff_s=0.5, jitter=False, honor_retry_after=False)
    asyncio.run(acall_with_retry(_async_flaky([_transient(99.0)]), policy=off, classify=_identity))
    assert clock == [4.0, 0.5]


def test_retry_policy_parses_and_serialises_new_fields() -> None:
    policy = RetryPolicy.from_mapping({"honor_retry_after": False, "max_retry_after_s": 7}, field_name="retry")
    assert policy.honor_retry_after is False
    assert policy.max_retry_after_s == 7.0
    mapping = policy.to_mapping()
    assert mapping["honor_retry_after"] is False
    assert mapping["max_retry_after_s"] == 7.0
    round_trip = RetryPolicy.from_mapping(mapping, field_name="retry")
    assert round_trip == policy


@pytest.mark.parametrize("raw", [{"max_retry_after_s": -1}, {"max_retry_after_s": "5"}, {"max_retry_after_s": True}])
def test_retry_policy_rejects_bad_max_retry_after(raw: dict[str, Any]) -> None:
    with pytest.raises(SpecBindingError, match="max_retry_after_s"):
        RetryPolicy.from_mapping(raw, field_name="retry")


def test_retry_policy_coerces_honor_flag_like_jitter() -> None:
    policy = RetryPolicy.from_mapping({"honor_retry_after": 0}, field_name="retry")
    assert policy.honor_retry_after is False


def test_retries_zero_is_one_attempt() -> None:
    policy = RetryPolicy.from_mapping({"retries": 0}, field_name="retry")
    assert policy.max_attempts == 1


def test_retries_and_max_attempts_together_is_an_error() -> None:
    with pytest.raises(SpecBindingError, match="not both"):
        RetryPolicy.from_mapping({"retries": 0, "max_attempts": 2}, field_name="retry")


def test_retries_zero_does_not_retry_a_429(clock: list[float]) -> None:
    n = {"c": 0}

    def boom() -> str:
        n["c"] += 1
        raise _transient(None)

    policy = RetryPolicy.from_mapping({"retries": 0, "backoff_s": 0, "jitter": False}, field_name="retry")
    with pytest.raises(ClassifiedFailure) as excinfo:
        call_with_retry(boom, policy=policy, classify=lambda exc: exc, idempotent=True)
    assert excinfo.value.code == "HTTP_429"
    assert n["c"] == 1
    assert clock == []


def test_context_length_failure_is_never_retried(clock: list[float]) -> None:
    response = httpx.Response(
        400,
        json={"error": {"code": "context_length_exceeded", "message": "maximum context length is 8192 tokens"}},
        request=httpx.Request("POST", "https://llm.example.test/v1/chat"),
    )

    def boom() -> str:
        raise httpx.HTTPStatusError("400", request=response.request, response=response)

    policy = RetryPolicy(max_attempts=4, backoff_s=0.1, jitter=False)
    with pytest.raises(ClassifiedFailure) as excinfo:
        call_with_retry(boom, policy=policy, classify=classify_llm_failure, idempotent=True)
    assert excinfo.value.code == "CONTEXT_LENGTH_EXCEEDED"
    assert excinfo.value.failure_class is FailureClass.APPLICATION
    assert excinfo.value.attempts == 1
    assert clock == []
