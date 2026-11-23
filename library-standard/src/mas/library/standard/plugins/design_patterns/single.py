#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Single-agent deterministic design pattern."""

from __future__ import annotations

from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.state import QProduct, RunLedger
from mas.runtime.schema.egress import EgressSymbol

from .base import _DeterministicBase
from .utils import dispatch_single


class DeterministicSingleAgentPlugin(_DeterministicBase):
    """Single-agent delegation — routes to one agent, no LLM loop."""

    plugin_id = "deterministic_single@v1"
    mode = "single"

    def evaluate_next(self, q: QProduct, run: RunLedger, *, config: KernelConfig) -> list[EgressSymbol]:
        st = self._state()
        if not st.participants:
            return self._no_participants(q)
        tool_results = self._extract_tool_results(run, q)
        if st.next_idx == 0:
            st.next_idx = 1
            return dispatch_single(self, q, run, config, st.original_task, st.participants[0])
        return self._finish(q, tool_results[-1] if tool_results else "Deterministic single run complete.")
