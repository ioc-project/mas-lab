#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Test retrier: never runs a run twice."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from mas.lab.benchmark.execution.roles import AttemptRecord, RetryDecision, reject_unknown_keys


class NeverRetrier:
    plugin_id = "never"

    def validate_params(self, params: dict[str, Any]) -> None:
        reject_unknown_keys(params or {}, set(), plugin_id=self.plugin_id)

    def decide(self, history: Sequence[AttemptRecord]) -> RetryDecision:
        return RetryDecision(False, 0.0, "never")
