#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Typed failure classes for LLM and tool calls.

Infra retry, the circuit breaker, and ingress governance all key off this
enum — not off exception-string matching.
"""

from __future__ import annotations

from enum import Enum


class FailureClass(str, Enum):
    """What kind of failure happened, independent of the transport.

    ``transient``
        The peer answered or the socket dropped mid-flight. A second
        identical call may succeed (HTTP 429/5xx, timeout, reset).
    ``unavailable``
        The peer is not there (connect refused, DNS, missing tool,
        open circuit). Retry a few times, then open the breaker.
    ``application``
        The call ran and returned a domain error (4xx business body,
        tool raised ValueError). Store in working memory; do not retry
        the same arguments unless a governance plugin says so.
    ``fatal``
        Will not heal without a config change (TLS verify, 401/403).
        Never retry.
    """

    TRANSIENT = "transient"
    UNAVAILABLE = "unavailable"
    APPLICATION = "application"
    FATAL = "fatal"


class ClassifiedFailure(RuntimeError):
    """Exception that already knows its :class:`FailureClass`."""

    def __init__(
        self,
        message: str,
        *,
        failure_class: FailureClass,
        code: str = "",
        attempts: int = 0,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_class = failure_class
        self.code = code or failure_class.value
        self.attempts = attempts
        # Server-provided wait before the next try (Retry-After), in seconds.
        self.retry_after_s = retry_after_s

    def with_attempts(self, attempts: int) -> ClassifiedFailure:
        return ClassifiedFailure(
            str(self),
            failure_class=self.failure_class,
            code=self.code,
            attempts=attempts,
            retry_after_s=self.retry_after_s,
        )


class CircuitOpenError(ClassifiedFailure):
    """Circuit breaker is open — fail fast, do not hit the peer."""

    def __init__(self, target: str, *, reset_timeout_s: float) -> None:
        super().__init__(
            f"circuit open for {target!r}; retry after {reset_timeout_s:.1f}s",
            failure_class=FailureClass.UNAVAILABLE,
            code="CIRCUIT_OPEN",
        )
        self.target = target
