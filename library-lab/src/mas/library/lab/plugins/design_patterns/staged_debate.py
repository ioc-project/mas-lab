#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Staged debate deterministic design pattern."""

from __future__ import annotations

from dataclasses import dataclass, field

from mas.library.standard.plugins.design_patterns.base import _DeterministicBase, _TurnState
from mas.library.standard.plugins.design_patterns.utils import dispatch_parallel, majority_vote
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.state import QProduct, RunLedger
from mas.runtime.schema.egress import EgressSymbol


@dataclass
class _DebateTurnState(_TurnState):
    """Extended turn state carrying round-1 peer outputs for round-2 revision."""

    debate_prev_round_outputs: list[str] = field(default_factory=list)


class DeterministicStagedDebatePlugin(_DeterministicBase):
    """Staged debate — round 1: parallel answers; round 2: revise using peer outputs; majority vote."""

    plugin_id = "deterministic_staged_debate@v1"
    mode = "staged_debate"

    def _make_state(self) -> _TurnState:
        return _DebateTurnState()

    def _debate_state(self) -> _DebateTurnState:
        return self._state()  # type: ignore[return-value]

    @staticmethod
    def _debate_prompt(original_task: str, prev_outputs: list[str]) -> str:
        peer = "\n\n".join(f"Peer answer {i + 1}:\n{o}" for i, o in enumerate(prev_outputs))
        return (
            "[Staged debate — round 2] Revise your answer using the peer outputs below.\n\n"
            f"Original task:\n{original_task}\n\n"
            f"Peer outputs:\n{peer if peer else '(none)'}"
        )

    def evaluate_next(self, q: QProduct, run: RunLedger, *, config: KernelConfig) -> list[EgressSymbol]:
        st = self._debate_state()
        if not st.participants:
            return self._no_participants(q)
        tool_results = self._extract_tool_results(run, q)
        new_results = tool_results[st.processed_tool_results :]
        if new_results:
            st.processed_tool_results = len(tool_results)

        if st.round_num == 1 and st.next_idx == 0:
            st.next_idx = 1
            return dispatch_parallel(self, q, run, config, st.original_task, st.participants)

        if st.round_num == 1:
            st.debate_prev_round_outputs = list(new_results) if new_results else list(tool_results)
            st.round_num = 2
            prompt = self._debate_prompt(st.original_task, st.debate_prev_round_outputs)
            return dispatch_parallel(self, q, run, config, prompt, st.participants)

        return self._finish(q, majority_vote(new_results if new_results else tool_results))
