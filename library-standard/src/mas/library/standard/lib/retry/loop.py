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


def _stamp_retry_metadata(result: T, *, attempts: int, last: ClassifiedFailure | None) -> T:
    """Copy extra retries onto an EngineIoReturn so events can record them."""
    extra = max(0, attempts - 1)
    if extra <= 0:
        return result
    from mas.runtime.schema.ingress import EngineIoReturn

    if not isinstance(result, EngineIoReturn):
        return result
    update: dict[str, Any] = {"retry_attempts": max(int(result.retry_attempts or 0), extra)}
    if last is not None:
        if not result.failure_class:
            update["failure_class"] = last.failure_class.value
        if not result.failure_code:
            update["failure_code"] = last.code
    return result.model_copy(update=update)


def _sleep_for(policy: RetryPolicy, attempt: int, classified: ClassifiedFailure) -> tuple[float, float | None]:
    """Seconds to wait before the next try, and the server hint when it was used."""
    delay = policy.delay_for(attempt)
    server = classified.retry_after_s
    if not policy.honor_retry_after or server is None:
        return delay, None
    return max(delay, server), server


def _should_retry(
    *,
    attempt: int,
    attempts: int,
    classified: ClassifiedFailure,
    policy: RetryPolicy,
    idempotent: bool,
) -> bool:
    if not attempt < attempts - 1 or not policy.allows(classified.failure_class, idempotent=idempotent):
        return False
    # A server asking for longer than the cap wants the caller to come back much later: retrying now would
    # only be refused again, so the failure is returned instead.
    hint = classified.retry_after_s
    return not (policy.honor_retry_after and hint is not None and hint > max(0.0, policy.max_retry_after_s))


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
            return _stamp_retry_metadata(result, attempts=attempt + 1, last=last)
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
            delay, server_delay = _sleep_for(policy, attempt, classified)
            log_retry(
                target=target,
                failure_class=classified.failure_class.value,
                failure_code=classified.code,
                attempt=attempt + 1,
                max_attempts=attempts,
                delay_s=delay,
                message=str(classified),
                server_delay_s=server_delay,
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
            return _stamp_retry_metadata(result, attempts=attempt + 1, last=last)
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
            delay, server_delay = _sleep_for(policy, attempt, classified)
            log_retry(
                target=target,
                failure_class=classified.failure_class.value,
                failure_code=classified.code,
                attempt=attempt + 1,
                max_attempts=attempts,
                delay_s=delay,
                message=str(classified),
                server_delay_s=server_delay,
            )
            await asyncio.sleep(delay)
    assert last is not None
    raise last
