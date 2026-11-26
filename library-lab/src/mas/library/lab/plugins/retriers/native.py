#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Bounded retry with exponential backoff; auth, TLS and spec errors are final."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from mas.lab.benchmark.execution.retry import lab_run_error_is_retryable, retry_after_seconds
from mas.lab.benchmark.execution.roles import AttemptRecord, RetryDecision, reject_unknown_keys

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_S = 2.0


class NativeRetrier:
    plugin_id = "native"

    def __init__(self) -> None:
        self.max_attempts = DEFAULT_MAX_ATTEMPTS
        self.backoff_s = DEFAULT_BACKOFF_S

    def validate_params(self, params: dict[str, Any]) -> None:
        params = params or {}
        reject_unknown_keys(params, {"max_attempts", "backoff_s"}, plugin_id=self.plugin_id)
        attempts = params.get("max_attempts", DEFAULT_MAX_ATTEMPTS)
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
            raise ValueError("retry.max_attempts must be an integer >= 1 (total tries, the first included)")
        backoff = params.get("backoff_s", DEFAULT_BACKOFF_S)
        if isinstance(backoff, bool) or not isinstance(backoff, (int, float)) or backoff < 0:
            raise ValueError("retry.backoff_s must be a number >= 0")
        self.max_attempts = attempts
        self.backoff_s = float(backoff)

    def decide(self, history: Sequence[AttemptRecord]) -> RetryDecision:
        if not history:
            return RetryDecision(False, 0.0, "no attempt yet")
        last = history[-1]
        if last.status != "error":
            return RetryDecision(False, 0.0, "complete")
        if last.fatal or not lab_run_error_is_retryable(last.error, status=last.status):
            return RetryDecision(False, 0.0, "not retryable")
        if len(history) >= self.max_attempts:
            return RetryDecision(False, 0.0, f"{self.max_attempts} attempts spent")
        delay = self.backoff_s * (2 ** (len(history) - 1))
        hinted = retry_after_seconds(last.error)
        if hinted is not None:
            delay = max(delay, hinted)
        return RetryDecision(True, delay, "transient failure")
