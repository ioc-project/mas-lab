#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Linear sequential deterministic design pattern."""

from __future__ import annotations

from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.state import QProduct, RunLedger
from mas.runtime.schema.egress import EgressSymbol

from .base import _DeterministicBase
from .utils import dispatch_single


class DeterministicLinearPlugin(_DeterministicBase):
    """Linear pipeline — delegates sequentially, each agent receives previous output."""

    plugin_id = "deterministic_linear@v1"
    mode = "linear"

    def evaluate_next(self, q: QProduct, run: RunLedger, config: KernelConfig) -> list[EgressSymbol]:
        st = self._state()
        if not st.participants:
            return self._no_participants(q)
        tool_results = self._extract_tool_results(run, q)
        if st.next_idx < len(st.participants):
            task = st.original_task if st.next_idx == 0 else (tool_results[-1] if tool_results else st.original_task)
            target = st.participants[st.next_idx]
            st.next_idx += 1
            return dispatch_single(self, q, run, config, task, target)
        return self._finish(q, tool_results[-1] if tool_results else "Deterministic linear run complete.")
