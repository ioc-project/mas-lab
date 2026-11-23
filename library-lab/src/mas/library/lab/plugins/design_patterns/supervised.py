#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Supervised deterministic design pattern."""

from __future__ import annotations

from mas.library.standard.plugins.design_patterns.base import _DeterministicBase
from mas.library.standard.plugins.design_patterns.utils import dispatch_parallel, dispatch_single
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.state import QProduct, RunLedger
from mas.runtime.schema.egress import EgressSymbol


class DeterministicSupervisedPlugin(_DeterministicBase):
    """Supervised — workers fan out in parallel; last participant reviews all outputs.

    With N delegates [w0, w1, ..., wN-2, supervisor]:
    - Step 1: workers w0..wN-2 receive the original task in parallel (like parallel fan-out).
    - Step 2: supervisor (wN-1) receives all worker outputs and synthesises a final answer.

    Differs from ``parallel``: outputs are not concatenated verbatim — a dedicated agent
    actively reviews and synthesises them.
    Differs from ``linear``: workers are independent, not a sequential chain.
    Differs from ``voting``: no election — the supervisor produces a synthesis, not a vote.
    """

    plugin_id = "deterministic_supervised@v1"
    mode = "supervised"

    def evaluate_next(self, q: QProduct, run: RunLedger, *, config: KernelConfig) -> list[EgressSymbol]:
        st = self._state()
        if not st.participants:
            return self._no_participants(q)
        tool_results = self._extract_tool_results(run, q)
        workers = st.participants[:-1]
        supervisor = st.participants[-1]

        # Step 1 — fan-out workers in parallel
        if st.round_num == 1 and st.next_idx == 0:
            if not workers:
                # Only one participant: treat it as both worker and supervisor
                st.round_num = 2
                return dispatch_single(self, q, run, config, st.original_task, supervisor)
            st.next_idx = 1
            return dispatch_parallel(self, q, run, config, st.original_task, workers)

        # Step 2 — supervisor reviews all worker outputs
        if st.round_num == 1:
            st.round_num = 2
            outputs = "\n\n".join(f"Worker {i + 1} ({workers[i]}):\n{o}" for i, o in enumerate(tool_results))
            review_prompt = (
                f"You are reviewing the outputs of {len(workers)} worker agent(s) "
                f"for the following task.\n\n"
                f"Task: {st.original_task}\n\n"
                f"Worker outputs:\n{outputs}\n\n"
                f"Synthesise these outputs into a single, coherent final answer."
            )
            return dispatch_single(self, q, run, config, review_prompt, supervisor)

        return self._finish(q, tool_results[-1] if tool_results else "Supervised review complete.")
