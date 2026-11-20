#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tree-of-Thoughts design pattern — TLA: DesignPatternScheduler.tla, TreeOfThoughtsPattern.tla."""

from __future__ import annotations

import json
from typing import Any

from mas.library.standard.plugins.design_patterns.cot import CotPlugin
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.state import DpState, QProduct, RunLedger
from mas.runtime.schema.egress import EgressSymbol, NoOp
from mas.runtime.schema.ingress import IngressSymbol, UserInputReceived


def _parse_thoughts(text: str) -> list[dict[str, Any]]:
    if not text.strip():
        return []
    try:
        parsed = json.loads(text.strip())
        if isinstance(parsed, dict) and "thoughts" in parsed:
            return list(parsed["thoughts"])
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass
    return [{"content": text[:200], "score": 0.5}]


class TreeOfThoughtsPlugin(CotPlugin):
    plugin_id = "tree_of_thoughts@v1"

    def protocol_lines(self, q: QProduct | None) -> list[str]:
        phase = (q.dp_data.get("tot_phase") if q else None) or "GENERATE"
        if phase == "GENERATE":
            return [
                "=== TREE-OF-THOUGHTS PROTOCOL ===",
                'Respond with JSON: {"thoughts": [{"content": "...", "score": 0.0-1.0}, ...]}',
                "Explore multiple reasoning paths before concluding.",
            ]
        if phase == "FINALIZE":
            return [
                "=== TREE-OF-THOUGHTS: SYNTHESIS ===",
                "Select the best reasoning path and give the final user-facing answer.",
            ]
        return []

    def handle_event(
        self,
        q: QProduct,
        run: RunLedger,
        event: IngressSymbol,
        *,
        config: KernelConfig,
    ) -> list[EgressSymbol]:
        if isinstance(event, UserInputReceived) and q.dp == DpState.IDLE:
            q.dp_data = {"tot_phase": "GENERATE", "tot_pass": 0, "best_score": 0.0}
        return super().handle_event(q, run, event, config=config)

    def _evaluate(self, q: QProduct, run: RunLedger, config: KernelConfig) -> list[EgressSymbol]:
        if q.dp != DpState.EVALUATING or not run.events:
            return [NoOp()]
        last = run.latest()
        if last is None:
            return [NoOp()]
        tot_pass = int(q.dp_data.get("tot_pass") or 0)
        max_passes = max(config.max_cot_pass, 2)

        if last.next_step == "STOP" and tot_pass < max_passes - 1:
            thoughts = _parse_thoughts(last.text)
            best = max((t.get("score", 0.0) for t in thoughts), default=0.0)
            q.dp_data["best_score"] = max(float(q.dp_data.get("best_score") or 0), best)
            q.dp_data["tot_pass"] = tot_pass + 1
            if tot_pass + 1 >= max_passes - 1:
                q.dp_data["tot_phase"] = "FINALIZE"
            q.cot_pass = tot_pass + 1
            q.scheduled_egress = "LLM_CALL"
            q.dp = DpState.EGRESS_PENDING
            from mas.runtime.kernel.egress_gate import emit_scheduled_egress

            return emit_scheduled_egress(q, run, config)

        return super()._evaluate(q, run, config)
