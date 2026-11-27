#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Current lab invoke: one MAS run, one attempt."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from mas.lab.benchmark.execution.completeness import invoke_is_complete
from mas.lab.benchmark.execution.retry import runner_outcome
from mas.lab.benchmark.execution.roles import AttemptResult, NoParams


class NativeExecutor(NoParams):
    plugin_id = "native"
    timeout_s: float = 300.0
    """Per-attempt bound, seconds. The host sets this from ``executor.timeout``."""

    def is_complete(self, result_dict: dict, *, status: str, error: str = "") -> bool:
        return invoke_is_complete(status=status, error=error)

    async def attempt(
        self,
        do_run: Callable[[], dict],
        *,
        before_attempt: Callable[[], Awaitable[None]] | None = None,
    ) -> AttemptResult:
        if before_attempt is not None:
            try:
                await before_attempt()
            except Exception as exc:
                # The MAS never starts on unprepared state, and a retry would not repair it.
                return AttemptResult({}, "error", str(exc), "", fatal=True)
        limit = float(self.timeout_s)
        try:
            result = await asyncio.wait_for(asyncio.to_thread(do_run), timeout=limit)
        except TimeoutError:
            return AttemptResult(
                {},
                "error",
                f"run timed out after {limit:g}s (executor.timeout)",
            )
        except Exception as exc:
            return AttemptResult({}, "error", str(exc))
        status, error, output = runner_outcome(result)
        return AttemptResult(result, status, error, output)
