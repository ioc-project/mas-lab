#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Verifier (propose-then-verify) deterministic design pattern."""

from __future__ import annotations

from mas.library.standard.plugins.design_patterns.base import _DeterministicBase
from mas.library.standard.plugins.design_patterns.utils import dispatch_single
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.state import QProduct, RunLedger
from mas.runtime.schema.egress import EgressSymbol


class DeterministicVerifierPlugin(_DeterministicBase):
    """Propose-then-verify — agent 0 proposes, agent 1 fact-checks the proposal."""

    plugin_id = "deterministic_verifier@v1"
    mode = "verifier"

    def evaluate_next(self, q: QProduct, run: RunLedger, *, config: KernelConfig) -> list[EgressSymbol]:
        st = self._state()
        if not st.participants:
            return self._no_participants(q)
        tool_results = self._extract_tool_results(run)

        if st.round_num == 1 and st.next_idx == 0:
            st.next_idx = 1
            return dispatch_single(self, q, run, config, st.original_task, st.participants[0])

        if st.round_num == 1:
            st.next_idx = 2
            st.round_num = 2
            proposal = tool_results[-1] if tool_results else ""
            verify_prompt = (
                f"Review and verify the proposal below. Check facts, logic, and completeness.\n\nProposal:\n{proposal}"
            )
            target = st.participants[1] if len(st.participants) > 1 else st.participants[0]
            return dispatch_single(self, q, run, config, verify_prompt, target)

        return self._finish(q, tool_results[-1] if tool_results else "Verification complete.")
