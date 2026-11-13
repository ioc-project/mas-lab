#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Structured logging for retries, on the shared ``mas.runtime.reliability`` logger."""

from __future__ import annotations

from mas.runtime.reliability.log import LOGGER, extra


def log_retry(
    *,
    target: str,
    failure_class: str,
    failure_code: str,
    attempt: int,
    max_attempts: int,
    delay_s: float,
    message: str,
) -> None:
    LOGGER.warning(
        "%s %s/%s failed (%s %s); retrying in %.3fs",
        target or "call",
        attempt,
        max_attempts,
        failure_class,
        failure_code or message,
        delay_s,
        extra=extra(
            target=target,
            failure_class=failure_class,
            failure_code=failure_code,
            attempt=attempt,
            max_attempts=max_attempts,
            delay_s=round(delay_s, 3),
            outcome="retry",
        ),
    )


def log_no_retry(
    *,
    target: str,
    failure_class: str,
    failure_code: str,
    attempt: int,
    max_attempts: int,
    message: str,
) -> None:
    LOGGER.warning(
        "%s failed (%s %s); not retrying: %s",
        target or "call",
        failure_class,
        failure_code,
        message,
        extra=extra(
            target=target,
            failure_class=failure_class,
            failure_code=failure_code,
            attempt=attempt,
            max_attempts=max_attempts,
            outcome="no_retry",
        ),
    )


def log_exhausted(
    *,
    target: str,
    failure_class: str,
    failure_code: str,
    attempt: int,
    max_attempts: int,
    message: str,
) -> None:
    LOGGER.error(
        "%s failed after %s/%s attempts (%s %s): %s",
        target or "call",
        attempt,
        max_attempts,
        failure_class,
        failure_code,
        message,
        extra=extra(
            target=target,
            failure_class=failure_class,
            failure_code=failure_code,
            attempt=attempt,
            max_attempts=max_attempts,
            outcome="exhausted",
        ),
    )


def log_recovered(*, target: str, attempts: int) -> None:
    if attempts <= 1:
        return
    LOGGER.info(
        "%s succeeded after %s attempts",
        target or "call",
        attempts,
        extra=extra(target=target, attempt=attempts, outcome="recovered"),
    )
