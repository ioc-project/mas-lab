#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Parallel fan-out deterministic design pattern."""

from __future__ import annotations

from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.state import QProduct, RunLedger
from mas.runtime.schema.egress import EgressSymbol

from .base import _DeterministicBase
from .utils import dispatch_parallel


class DeterministicParallelPlugin(_DeterministicBase):
    """Parallel fan-out — all agents get same task, outputs concatenated."""

    plugin_id = "deterministic_parallel@v1"
    mode = "parallel"

    def evaluate_next(self, q: QProduct, run: RunLedger, *, config: KernelConfig) -> list[EgressSymbol]:
        st = self._state()
        if not st.participants:
            return self._no_participants(q)
        tool_results = self._extract_tool_results(run, q)
        if st.round_num == 1 and st.next_idx == 0:
            st.next_idx = 1
            return dispatch_parallel(self, q, run, config, st.original_task, st.participants)
        merged = "\n\n".join(f"=== {i + 1} ===\n{x}" for i, x in enumerate(tool_results))
        return self._finish(q, merged or "Deterministic parallel run complete.")
