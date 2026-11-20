#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Voting (majority-vote) deterministic design pattern."""

from __future__ import annotations

from mas.library.standard.plugins.design_patterns.base import _DeterministicBase
from mas.library.standard.plugins.design_patterns.utils import dispatch_parallel, majority_vote
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.state import QProduct, RunLedger
from mas.runtime.schema.egress import EgressSymbol


class DeterministicVotingPlugin(_DeterministicBase):
    """Parallel fan-out with majority vote on answers."""

    plugin_id = "deterministic_voting@v1"
    mode = "voting"

    def evaluate_next(self, q: QProduct, run: RunLedger, *, config: KernelConfig) -> list[EgressSymbol]:
        st = self._state()
        if not st.participants:
            return self._no_participants(q)
        tool_results = self._extract_tool_results(run, q)
        if st.round_num == 1 and st.next_idx == 0:
            st.next_idx = 1
            return dispatch_parallel(self, q, run, config, st.original_task, st.participants)
        return self._finish(q, majority_vote(tool_results))
