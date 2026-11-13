#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared retry loop for LLM HTTP and tool dispatch."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from mas.library.standard.lib.retry.log import log_exhausted, log_no_retry, log_recovered, log_retry
from mas.library.standard.lib.retry.policy import RetryPolicy
from mas.runtime.reliability.classes import CircuitOpenError, ClassifiedFailure

T = TypeVar("T")
Classify = Callable[[BaseException], ClassifiedFailure]


def _should_retry(
    *,
    attempt: int,
    attempts: int,
    classified: ClassifiedFailure,
    policy: RetryPolicy,
    idempotent: bool,
) -> bool:
    return attempt < attempts - 1 and policy.allows(classified.failure_class, idempotent=idempotent)


def _finish_failure(
    *,
    classified: ClassifiedFailure,
    attempt: int,
    attempts: int,
    breaker: Any | None,
    target: str,
) -> None:
    if breaker is not None and target:
        breaker.record_failure(target, classified.failure_class)
    log_fn = log_exhausted if attempt > 0 else log_no_retry
    log_fn(
        target=target,
        failure_class=classified.failure_class.value,
        failure_code=classified.code,
        attempt=attempt + 1,
        max_attempts=attempts,
        message=str(classified),
    )


def as_classified(exc: BaseException, classify: Classify) -> ClassifiedFailure:
    if isinstance(exc, ClassifiedFailure):
        return exc
    return classify(exc)


def call_with_retry(
    fn: Callable[[], T],
    *,
    policy: RetryPolicy,
    classify: Classify,
    breaker: Any | None = None,
    target: str = "",
    idempotent: bool = True,
) -> T:
    """Run ``fn`` up to ``policy.max_attempts`` times.

    The circuit breaker (if any) is consulted *before* the first attempt and
    records failures that survive the retry budget. Each retry and the final
    exhaustion is logged on ``mas.runtime.reliability``.
    """
    if breaker is not None and target:
        breaker.before_call(target)
    last: ClassifiedFailure | None = None
    attempts = max(1, policy.max_attempts)
    for attempt in range(attempts):
        try:
            result = fn()
            if breaker is not None and target:
                breaker.record_success(target)
            log_recovered(target=target, attempts=attempt + 1)
            return result
        except CircuitOpenError:
            raise
        except Exception as exc:
            classified = as_classified(exc, classify).with_attempts(attempt + 1)
            last = classified
            if not _should_retry(
                attempt=attempt,
                attempts=attempts,
                classified=classified,
                policy=policy,
                idempotent=idempotent,
            ):
                _finish_failure(
                    classified=classified,
                    attempt=attempt,
                    attempts=attempts,
                    breaker=breaker,
                    target=target,
                )
                raise classified from exc
            delay = policy.delay_for(attempt)
            log_retry(
                target=target,
                failure_class=classified.failure_class.value,
                failure_code=classified.code,
                attempt=attempt + 1,
                max_attempts=attempts,
                delay_s=delay,
                message=str(classified),
            )
            time.sleep(delay)
    assert last is not None
    raise last


async def acall_with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
    classify: Classify,
    breaker: Any | None = None,
    target: str = "",
    idempotent: bool = True,
) -> T:
    """Async twin of :func:`call_with_retry` (``ainvoke`` / async HTTP)."""
    if breaker is not None and target:
        breaker.before_call(target)
    last: ClassifiedFailure | None = None
    attempts = max(1, policy.max_attempts)
    for attempt in range(attempts):
        try:
            result = await fn()
            if breaker is not None and target:
                breaker.record_success(target)
            log_recovered(target=target, attempts=attempt + 1)
            return result
        except CircuitOpenError:
            raise
        except Exception as exc:
            classified = as_classified(exc, classify).with_attempts(attempt + 1)
            last = classified
            if not _should_retry(
                attempt=attempt,
                attempts=attempts,
                classified=classified,
                policy=policy,
                idempotent=idempotent,
            ):
                _finish_failure(
                    classified=classified,
                    attempt=attempt,
                    attempts=attempts,
                    breaker=breaker,
                    target=target,
                )
                raise classified from exc
            delay = policy.delay_for(attempt)
            log_retry(
                target=target,
                failure_class=classified.failure_class.value,
                failure_code=classified.code,
                attempt=attempt + 1,
                max_attempts=attempts,
                delay_s=delay,
                message=str(classified),
            )
            await asyncio.sleep(delay)
    assert last is not None
    raise last
